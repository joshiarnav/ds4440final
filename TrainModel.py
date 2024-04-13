import torch
import numpy as np
from time import time
from DataSet import TrainSampler, EvaluateSampler, collate_fn

#Mixup class, implements mixup augmentation by generating random coefficients of size batch_size
class Mixup(object):
    def __init__(self, mixup_alpha, random_seed=1234):
        self.mixup_alpha = mixup_alpha
        self.random_state = np.random.RandomState(random_seed)

    def get_lambda(self, batch_size):
        mixup_lambdas = []
        for n in range(0, batch_size, 2):
            #uses beta distribution to generate random coefficients
            lam = self.random_state.beta(self.mixup_alpha, self.mixup_alpha, 1)[0]
            mixup_lambdas.append(lam)
            mixup_lambdas.append(1. - lam)

        return np.array(mixup_lambdas)


num_workers = 8 #follows format from given code. Change?
device = 'cuda' if (torch.cuda.is_available()) else 'cpu'
learning_rate=1e-4
stop_iteration = 10000
holdout_fold = 1
batch_size = 32

def train(model, dataset):
    if device == 'cuda':
        print('GPU number: {}'.format(torch.cuda.device_count()))
        model = torch.nn.DataParallel(model)
        model.to(device)


    ############################BATCHING######################################
    train_sampler = TrainSampler(
        data=dataset.data,
        holdout_fold=holdout_fold,
        batch_size=batch_size * 2) #for mixup

    validate_sampler = EvaluateSampler(
        hdf5_path=dataset.data,
        holdout_fold=holdout_fold,
        batch_size=batch_size)

    train_loader = torch.utils.data.DataLoader(dataset=dataset,
        batch_sampler=train_sampler, collate_fn=collate_fn,
        num_workers=num_workers, pin_memory=True)

    validate_loader = torch.utils.data.DataLoader(dataset=dataset,
        batch_sampler=validate_sampler, collate_fn=collate_fn,
        num_workers=num_workers, pin_memory=True)

    #ISSUE: do we need to store test data???
    #They just use validate!!! (i guess this is ok cause we're technically tuning?)


    #COLLATE_FN
    """Collate data.
    Args:
      list_data_dict, e.g., [{'audio_name': str, 'waveform': (clip_samples,), ...},
                             {'audio_name': str, 'waveform': (clip_samples,), ...},
                             ...]
    Returns:
      np_data_dict, dict, e.g.,
          {'audio_name': (batch_size,), 'waveform': (batch_size, clip_samples), ...}
    """

    ########################################################################

    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate, betas=(0.9, 0.999),
        eps=1e-08, weight_decay=0., amsgrad=True)

    mixup_augmenter = Mixup(mixup_alpha=1.)


    #TODO: IMPORTANT: SAVING

    # Train on mini batches
    iteration = 0
    train_bgn_time = time()
    for batch_data_dict in train_loader:
        #print validation accuracy every 200 iterations
        if iteration % 200 == 0 and iteration > 0:
            print('------------------------------------')
            print('Iteration: {}'.format(iteration))

            train_fin_time = time()

            statistics = evaluate(validate_loader)
            print('Validate accuracy: {:.3f}'.format(statistics['accuracy']))

            # statistics_container.append(iteration, statistics, 'validate')
            # statistics_container.dump()

            train_time = train_fin_time - train_bgn_time
            validate_time = time() - train_fin_time

            print(
                'Train time: {:.3f} s, validate time: {:.3f} s'
                ''.format(train_time, validate_time))

            train_bgn_time = time()


        batch_data_dict['mixup_lambda'] = mixup_augmenter.get_lambda(len(batch_data_dict['waveform']))

        # Move data to GPU
        for key in batch_data_dict.keys():
            batch_data_dict[key] = batch_data_dict[key].to(device)

        # Train
        model.train()

        #MIXUP
        batch_output_dict = model(batch_data_dict['waveform'],
            batch_data_dict['mixup_lambda'])
        """{'clipwise_output': (batch_size, classes_num), ...}"""

        mixed_target = (batch_data_dict['target'][0 :: 2].transpose(0, -1) * batch_data_dict['mixup_lambda'][0 :: 2] + \
            batch_data_dict['target'][1 :: 2].transpose(0, -1) * batch_data_dict['mixup_lambda'][1 :: 2]).transpose(0, -1)
        batch_target_dict = {'target': mixed_target}
        """{'target': (batch_size, classes_num)}"""

        loss = - torch.mean(batch_target_dict['target'] * batch_output_dict['clipwise_output'])
        print(iteration, loss)

        # Backward
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        if iteration == stop_iteration:
            break

        iteration += 1



###########EVALUATION################

def append_to_dict(dict, key, value):
    if key not in dict:
        dict[key] = []
    dict[key].append(value)

def evaluate(model, data_loader):
    output_dict = {}

    # Forward data to a model in mini-batches
    for n, batch_data_dict in enumerate(data_loader):
        # print(n)
        batch_waveform = batch_data_dict['waveform'].to(device)

        with torch.no_grad():
            model.eval()
            batch_output = model(batch_waveform)

        append_to_dict(output_dict, 'filename', batch_data_dict['filename'])

        append_to_dict(output_dict, 'clipwise_output',
            batch_output['clipwise_output'].data.cpu().numpy())

        # if return_input:
        #     append_to_dict(output_dict, 'waveform', batch_data_dict['waveform'])

        if 'target' in batch_data_dict.keys():
            append_to_dict(output_dict, 'target', batch_data_dict['target'])

    for key in output_dict.keys():
        output_dict[key] = np.concatenate(output_dict[key], axis=0)


    clipwise_output = output_dict['clipwise_output']    # (audios_num, classes_num)
    target = output_dict['target']    # (audios_num, classes_num)

    #cm = metrics.confusion_matrix(np.argmax(target, axis=-1), np.argmax(clipwise_output, axis=-1), labels=None)

    #Calculate accuracy
    N = target.shape[0]
    accuracy = np.sum(np.argmax(target, axis=-1) == np.argmax(clipwise_output, axis=-1)) / N

    statistics = {'accuracy': accuracy}

    return statistics
