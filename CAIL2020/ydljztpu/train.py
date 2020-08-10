import argparse
import gzip
import os
import pickle
from os.path import join
from tqdm import tqdm
from transformers import BertModel, BertTokenizer
from transformers import BertConfig as BC
import json

import torch
from torch import nn
from transformers.optimization import AdamW, get_linear_schedule_with_warmup
from model import *
from tools.utils import convert_to_tokens
from data_iterator_pack import IGNORE_INDEX
import numpy as np
import queue
import random
from config import set_config
from data_helper import DataHelper
from data_process import InputFeatures,Example
from typing import Dict
import argparse
import json
import os
from copy import deepcopy
from types import SimpleNamespace

import torch
from torch import nn
from torch.optim import Adam
from torch.utils.data import DataLoader, RandomSampler
from torch.utils.data.distributed import DistributedSampler
from tqdm import tqdm, trange
from transformers.optimization import (
    AdamW, get_linear_schedule_with_warmup, get_constant_schedule)

from data import Data
#from evaluate import evaluate, calculate_accuracy_f1, get_labels_from_file
from model import BertSupportNetX
from utils import get_csv_logger, get_path
#from vocab import build_vocab
import numpy as np
import os
import time
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import torch_xla
import torch_xla.core.xla_model as xm
import torch_xla.debug.metrics as met
import torch_xla.distributed.parallel_loader as pl
import torch_xla.distributed.xla_multiprocessing as xmp
import torch_xla.utils.utils as xu

try:
    from apex import amp
except Exception:
    print('Apex not import!')

#from data_process import read_examples, convert_examples_to_features
from evaluate.evaluate import eval
from utils import get_path,get_csv_logger

MODEL_MAP={
    "bert":BertSupportNetX
}

class Trainer:
    """Trainer for SMP-CAIL2020-Argmine.
    """
    def __init__(self,
                 model, data_loader: Dict[str, DataLoader], device, config):
        """Initialize trainer with model, data, device, and config.
        Initialize optimizer, scheduler, criterion.

        Args:
            model: model to be evaluated
            data_loader: dict of torch.utils.data.DataLoader
            device: torch.device('cuda') or torch.device('cpu')
            config:
                config.experiment_name: experiment name
                config.model_type: 'bert' or 'rnn'
                config.lr: learning rate for optimizer
                config.num_epoch: epoch number
                config.num_warmup_steps: warm-up steps number
                config.gradient_accumulation_steps: gradient accumulation steps
                config.max_grad_norm: max gradient norm

        """
        self.model = model
        self.device = device
        self.config = config
        self.data_loader = data_loader
        self.config.num_training_steps = config.num_epoch * (
            len(data_loader['train']) // config.batch_size)
        self.optimizer = self._get_optimizer()
        self.scheduler = self._get_scheduler()
        # self.criterion = nn.CrossEntropyLoss()
        self.criterion = nn.CrossEntropyLoss(reduction='mean', ignore_index=IGNORE_INDEX)  # 交叉熵损失
        self.binary_criterion = nn.BCEWithLogitsLoss(reduction='mean')  # 二元损失
        self.sp_loss_fct = nn.BCEWithLogitsLoss(reduction='none')  # 用于sp，平均值自己算

    def _get_optimizer(self):
        """Get optimizer for different models.

        Returns:
            optimizer
        """
        if self.config.model_type == 'bert':
            no_decay = ['bias', 'gamma', 'beta']
            optimizer_parameters = [
                {'params': [p for n, p in self.model.named_parameters()
                            if not any(nd in n for nd in no_decay)],
                 'weight_decay_rate': 0.01},
                {'params': [p for n, p in self.model.named_parameters()
                            if any(nd in n for nd in no_decay)],
                 'weight_decay_rate': 0.0}]
            optimizer = AdamW(
                optimizer_parameters,
                lr=self.config.lr,
                betas=(0.9, 0.999),
                weight_decay=1e-8,
                correct_bias=False)
        else:  # rnn
            optimizer = Adam(self.model.parameters(), lr=self.config.lr)
        return optimizer

    def _get_scheduler(self):
        """Get scheduler for different models.

        Returns:
            scheduler
        """
        if self.config.model_type == 'bert':
            scheduler = get_linear_schedule_with_warmup(
                self.optimizer,
                num_warmup_steps=self.config.num_warmup_steps,
                num_training_steps=self.config.num_training_steps)
        else:  # rnn
            scheduler = get_constant_schedule(self.optimizer)
        return scheduler

    def _evaluate_for_train_valid(self):
        """Evaluate model on train and valid set and get acc and f1 score.

        Returns:
            train_acc, train_f1, valid_acc, valid_f1
        """
        train_predictions = evaluate(
            model=self.model, data_loader=self.data_loader['valid_train'],
            device=self.device)
        valid_predictions = evaluate(
            model=self.model, data_loader=self.data_loader['valid_valid'],
            device=self.device)
        train_answers = get_labels_from_file(self.config.train_file_path)
        valid_answers = get_labels_from_file(self.config.valid_file_path)
        train_acc, train_f1 = calculate_accuracy_f1(
            train_answers, train_predictions)
        valid_acc, valid_f1 = calculate_accuracy_f1(
            valid_answers, valid_predictions)
        return train_acc, train_f1, valid_acc, valid_f1

    def _epoch_evaluate_update_description_log(
            self, tqdm_obj, logger, epoch, exam):
        """Evaluate model and update logs for epoch.

        Args:
            tqdm_obj: tqdm/trange object with description to be updated
            logger: logging.logger
            epoch: int

        Return:
            train_acc, train_f1, valid_acc, valid_f1
        """
        # Evaluate model for train and valid set
        # results = self._evaluate_for_train_valid()
        # train_acc, train_f1, valid_acc, valid_f1 = results
        # step_count = 999
        self.predict(self.model, tqdm_obj, exam,
                join(self.config.prediction_path, 'pred_epoch_{}.json'.format(epoch)))
        results = eval(join(self.config.prediction_path, 'pred_epoch_{}.json'.format(epoch)),
             self.config.valid_file_path)

        # Update tqdm description for command line
        # tqdm_obj.set_description(
        #     'Epoch: {:d}, train_acc: {:.6f}, train_f1: {:.6f}, '
        #     'valid_acc: {:.6f}, valid_f1: {:.6f}, '.format(
        #         epoch, train_acc, train_f1, valid_acc, valid_f1))
        # # Logging
        # logger.info(','.join([str(epoch)] + [str(s) for s in results]))
        return results

    def save_model(self, filename):
        """Save model to file.

        Args:
            filename: file name
        """
        torch.save(self.model.state_dict(), filename)

    def compute_loss(self, batch, start_logits, end_logits, type_logits, sp_logits, start_position, end_position):
        loss1 = self.criterion(start_logits, batch['y1']) + self.criterion(end_logits, batch['y2'])
        loss2 = args.type_lambda * self.criterion(type_logits, batch['q_type'])
        sent_num_in_batch = batch["start_mapping"].sum()
        loss3 = args.sp_lambda * self.sp_loss_fct(sp_logits.view(-1),
                                             batch['is_support'].float().view(-1)).sum() / sent_num_in_batch
        loss = loss1 + loss2 + loss3
        return loss, loss1, loss2, loss3

    @torch.no_grad()
    def predict(self, model, dataloader, exam, prediction_file, need_sp_logit_file=False):

        model.eval()
        answer_dict = {}
        sp_dict = {}
        # dataloader.refresh()
        total_test_loss = [0] * 5

        tqdm_obj = tqdm(dataloader, ncols=80)
        for step, batch in enumerate(tqdm_obj):
            batch = tuple(t.to(self.device) for t in batch)
            # batch['context_mask'] = batch['context_mask'].float()
            start_logits, end_logits, type_logits, sp_logits, start_position, end_position = model(*batch)
            loss1 = self.criterion(start_logits, batch[-4]) + self.criterion(end_logits, batch[-3])
            loss2 = self.config.type_lambda * self.criterion(type_logits, batch[-2])

            sp_value = self.sp_loss_fct(sp_logits.view(-1), batch[-1].float().view(-1)).sum()
            sent_num_in_batch = batch[-7].sum()
            loss3 = self.config.sp_lambda * sp_value / sent_num_in_batch

            loss = loss1 + loss2 + loss3
            loss_list = [loss, loss1, loss2, loss3]

            for i, l in enumerate(loss_list):
                if not isinstance(l, int):
                    total_test_loss[i] += l.item()

            answer_dict_ = convert_to_tokens(batch, batch[-5], start_position.data.cpu().numpy().tolist(),
                                             end_position.data.cpu().numpy().tolist(),
                                             np.argmax(type_logits.data.cpu().numpy(), 1))
            answer_dict.update(answer_dict_)

            predict_support_np = torch.sigmoid(sp_logits).data.cpu().numpy()
            for i in range(predict_support_np.shape[0]):
                cur_sp_pred = []
                cur_id = str(batch[-5][i])
                exam_ = exam[step]
                cur_sp_logit_pred = []  # for sp logit output
                for j in range(predict_support_np.shape[1]):
                    if j >= len(exam_.sent_names):
                        break
                    if need_sp_logit_file:
                        temp_title, temp_id = exam_.sent_names[j]
                        cur_sp_logit_pred.append((temp_title, temp_id, predict_support_np[i, j]))
                    if predict_support_np[i, j] > self.config.sp_threshold:
                        cur_sp_pred.append(exam_.sent_names[j])
                sp_dict.update({cur_id: cur_sp_pred})

        new_answer_dict = {}
        for key, value in answer_dict.items():
            new_answer_dict[key] = value.replace(" ", "")
        prediction = {'answer': new_answer_dict, 'sp': sp_dict}
        with open(prediction_file, 'w', encoding='utf8') as f:
            json.dump(prediction, f, indent=4, ensure_ascii=False)

        for i, l in enumerate(total_test_loss):
            print("Test Loss{}: {}".format(i, l / len(dataloader)))
        #test_loss_record.append(sum(total_test_loss[:3]) / len(dataloader))

    def train(self):
        """Train model on train set and evaluate on train and valid set.

        Returns:
            state dict of the best model with highest valid f1 score
        """
        epoch_logger = get_csv_logger(
            os.path.join(self.config.log_path,
                         self.config.experiment_name + '-epoch.csv'),
            title='epoch,train_acc,train_f1,valid_acc,valid_f1')
        step_logger = get_csv_logger(
            os.path.join(self.config.log_path,
                         self.config.experiment_name + '-step.csv'),
            title='step,loss')
        trange_obj = trange(self.config.num_epoch, desc='Epoch', ncols=120)
        # self._epoch_evaluate_update_description_log(
        #     tqdm_obj=trange_obj, logger=epoch_logger, epoch=0)
        best_model_state_dict, best_train_f1, global_step = None, 0, 0
        for epoch, _ in enumerate(trange_obj):
            self.model.train()
            tqdm_obj = tqdm(self.data_loader['train'], ncols=80)
            for step, batch in enumerate(tqdm_obj):
                batch = tuple(t.to(self.device) for t in batch)
                # loss = self.criterion(logits, batch[-1])
                start_logits, end_logits, type_logits, sp_logits, start_position, end_position = self.model(*batch)
                loss1 = self.criterion(start_logits, batch[-4]) + self.criterion(end_logits, batch[-3])
                loss2 = self.config.type_lambda * self.criterion(type_logits, batch[-2])

                sp_value = self.sp_loss_fct(sp_logits.view(-1), batch[-1].float().view(-1)).sum()
                sent_num_in_batch = batch[-7].sum()
                loss3 = self.config.sp_lambda * sp_value / sent_num_in_batch

                loss = loss1 + loss2 + loss3

                # if self.config.gradient_accumulation_steps > 1:
                #     loss = loss / self.config.gradient_accumulation_steps
                # self.optimizer.zero_grad()
                # loss.backward()
                loss.backward()

                if (step + 1) % self.config.gradient_accumulation_steps == 0:
                    torch.nn.utils.clip_grad_norm_(
                        self.model.parameters(), self.config.max_grad_norm)
                    #after 梯度累加的基本思想在于，在优化器更新参数前，也就是执行 optimizer.step() 前，进行多次反向传播，是的梯度累计值自动保存在 parameter.grad 中，最后使用累加的梯度进行参数更新。
                    self.optimizer.step()
                    self.scheduler.step()
                    self.optimizer.zero_grad()
                    global_step += 1
                    tqdm_obj.set_description('loss: {:.6f} {:.6f} {:.6f}'.format(loss1.item(), loss2.item(), loss3.item()))
                    step_logger.info(str(global_step) + ',' + str(loss.item()))

            train_results = self._epoch_evaluate_update_description_log(
                tqdm_obj=self.data_loader['valid_train'], logger=epoch_logger, epoch=epoch + 1, exam =self.data_loader['train_exam'] )

            valid_results = self._epoch_evaluate_update_description_log(
                tqdm_obj=self.data_loader['valid_valid'], logger=epoch_logger, epoch=epoch + 1,
                exam=self.data_loader['valid_exam'])

            results = (train_results['f1'],train_results['sp_f1'],train_results['joint_f1'],valid_results['f1'],valid_results['sp_f1'],valid_results['joint_f1'])
            self.save_model(os.path.join(
                self.config.model_path, self.config.experiment_name,
                self.config.model_type + '-' + str(epoch + 1) + '.bin'))

            if results[-4] > best_train_f1:
                best_model_state_dict = deepcopy(self.model.state_dict())
                best_train_f1 = results[-4]

        return best_model_state_dict

    def valid(self):
        """Train model on train set and evaluate on train and valid set.

                Returns:
                    state dict of the best model with highest valid f1 score
                """
        epoch_logger = get_csv_logger(
            os.path.join(self.config.log_path,
                         self.config.experiment_name + '-epoch.csv'),
            title='epoch,train_acc,train_f1,valid_acc,valid_f1')
        step_logger = get_csv_logger(
            os.path.join(self.config.log_path,
                         self.config.experiment_name + '-step.csv'),
            title='step,loss')
        # trange_obj = trange(self.config.num_epoch, desc='Epoch', ncols=120)
        # # self._epoch_evaluate_update_description_log(
        # #     tqdm_obj=trange_obj, logger=epoch_logger, epoch=0)
        # best_model_state_dict, best_train_f1, global_step = None, 0, 0


        train_results = self._epoch_evaluate_update_description_log(
            tqdm_obj=self.data_loader['valid_train'], logger=epoch_logger, epoch=-1 + 1,
            exam=self.data_loader['train_exam'])

        valid_results = self._epoch_evaluate_update_description_log(
            tqdm_obj=self.data_loader['valid_valid'], logger=epoch_logger, epoch=-1 + 1,
            exam=self.data_loader['valid_exam'])

        results = (train_results['f1'], train_results['sp_f1'], train_results['joint_f1'], valid_results['f1'],
                   valid_results['sp_f1'], valid_results['joint_f1'])
        # self.save_model(os.path.join(
        #     self.config.model_path, self.config.experiment_name,
        #     self.config.model_type + '-' + str(epoch + 1) + '.bin'))
        #
        # if results[-4] > best_train_f1:
        # best_model_state_dict = deepcopy(self.model.state_dict())
        # best_train_f1 = results[-4]
        return results

def set_seed(args):
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if args.n_gpu > 0:
        torch.cuda.manual_seed_all(args.seed)


def dispatch(context_encoding, context_mask, batch, device):
    batch['context_encoding'] = context_encoding.cuda(device)
    batch['context_mask'] = context_mask.float().cuda(device)
    return batch


def compute_loss(batch, start_logits, end_logits, type_logits, sp_logits, start_position, end_position):
    loss1 = criterion(start_logits, batch['y1']) + criterion(end_logits, batch['y2'])
    loss2 = args.type_lambda * criterion(type_logits, batch['q_type'])
    sent_num_in_batch = batch["start_mapping"].sum()
    loss3 = args.sp_lambda * sp_loss_fct(sp_logits.view(-1), batch['is_support'].float().view(-1)).sum() / sent_num_in_batch
    loss = loss1 + loss2 + loss3
    return loss, loss1, loss2, loss3




def train_epoch(data_loader, model, logger, predict_during_train=False, epoch=1):
    model.train()
    pbar = tqdm(total=len(data_loader))
    epoch_len = len(data_loader)
    step_count = 0
    predict_step = epoch_len // 2
    while not data_loader.empty():
        step_count += 1
        batch = next(iter(data_loader))
        batch['context_mask'] = batch['context_mask'].float()
        train_batch(model, batch)
        # del batch
        if predict_during_train and (step_count % predict_step == 0):
            predict(model, eval_dataset, dev_example_dict, dev_feature_dict,
                     join(args.prediction_path, 'pred_seed_{}_epoch_{}_{}.json'.format(args.seed, epc, step_count)))
            eval(join(args.prediction_path, 'pred_seed_{}_epoch_{}_{}.json'.format(args.seed, epc, step_count)), args.validdata)
            model_to_save = model.module if hasattr(model, 'module') else model
            torch.save(model_to_save.state_dict(), join(args.checkpoint_path, "ckpt_seed_{}_epoch_{}_{}.pkl".format(args.seed, epc, step_count)))
            model.train()
        pbar.update(1)

    predict(model, eval_dataset, dev_example_dict, dev_feature_dict,
             join(args.prediction_path, 'pred_seed_{}_epoch_{}_99999.json'.format(args.seed, epc)))
    results = eval(join(args.prediction_path, 'pred_seed_{}_epoch_{}_99999.json'.format(args.seed, epc)), args.validdata)
    # Logging
    keys='em,f1,prec,recall,sp_em,sp_f1,sp_prec,sp_recall,joint_em,joint_f1,joint_prec,joint_recall'.split(',')
    logger.info(','.join([str(epoch)] + [str(results[s]) for s in keys]))
    model_to_save = model.module if hasattr(model, 'module') else model
    torch.save(model_to_save.state_dict(), join(args.checkpoint_path, "ckpt_seed_{}_epoch_{}_99999.pkl".format(args.seed, epc)))


def train_batch(model, batch):
    global global_step, total_train_loss

    start_logits, end_logits, type_logits, sp_logits, start_position, end_position = model(batch)
    loss_list = compute_loss(batch, start_logits, end_logits, type_logits, sp_logits, start_position, end_position)
    loss_list = list(loss_list)
    if args.gradient_accumulation_steps > 1:
        # loss_list[0] = loss_list[0] / args.gradient_accumulation_steps
        loss_list[0] /= args.gradient_accumulation_steps
    
    if args.fp16:
        with amp.scale_loss(loss_list[0], optimizer) as scaled_loss:
            scaled_loss.backward()
    else:
        # loss_list[0].backward()
        loss_list[0].backward()

    if (global_step + 1) % args.gradient_accumulation_steps == 0:
        optimizer.step()
        scheduler.step()
        optimizer.zero_grad()

    global_step += 1

    for i, l in enumerate(loss_list):
        if not isinstance(l, int):
            total_train_loss[i] += l.item()

    if global_step % VERBOSE_STEP == 0:
        print("{} -- In Epoch{}: ".format(args.name, epc))
        for i, l in enumerate(total_train_loss):
            print("Avg-LOSS{}/batch/step: {}".format(i, l / VERBOSE_STEP))
        total_train_loss = [0] * 5


def main(config_file='config/bert_config.json'):
    """Main method for training.

    Args:
        config_file: in config dir
    """
    # 0. Load config and mkdir
    with open(config_file) as fin:
        config = json.load(fin, object_hook=lambda d: SimpleNamespace(**d))
    get_path(os.path.join(config.model_path, config.experiment_name))
    get_path(config.log_path)
    get_path(config.prediction_path)
    get_path(config.checkpoint_path)
    if config.model_type == 'rnn':  # build vocab for rnn
        build_vocab(file_in=config.all_train_file_path,
                    file_out=os.path.join(config.model_path, 'vocab.txt'))
    # 1. Load data
    data = Data(vocab_file=os.path.join(config.model_path, 'vocab.txt'),
                max_seq_len=config.max_seq_len,
                model_type=config.model_type, config=config)
    datasets = data.load_train_and_valid_files(
        train_file=config.train_file_path,
        valid_file=config.valid_file_path)
    train_set, valid_set_train, valid_set_valid, train_exam, valid_exam = datasets
    if torch.cuda.is_available():
        device = torch.device('cuda')
        # device = torch.device('cpu')
        # torch.distributed.init_process_group(backend="nccl")
        # sampler_train = DistributedSampler(train_set)
        sampler_train = RandomSampler(train_set)
    else:
        device = torch.device('cpu')
        sampler_train = RandomSampler(train_set)

    # TPU
    device = xm.xla_device()
    sampler_train = torch.utils.data.distributed.DistributedSampler(
        train_set,
        num_replicas=xm.xrt_world_size(),
        rank=xm.get_ordinal(),
        shuffle=True)

    data_loader = {
        'train': DataLoader(
            train_set, sampler=sampler_train, batch_size=config.batch_size),
        'valid_train': DataLoader(
            valid_set_train, batch_size=config.batch_size, shuffle=False),
        'valid_valid': DataLoader(
            valid_set_valid, batch_size=config.batch_size, shuffle=False),
        'train_exam': train_exam,
        'valid_exam': valid_exam
    }
    # 2. Build model
    # TPU

    device = xm.xla_device()
    model = WRAPPED_MODEL
    model.to(device)


    if config.model_type == 'bert':
        no_decay = ['bias', 'gamma', 'beta']
        optimizer_parameters = [
            {'params': [p for n, p in model.named_parameters()
                        if not any(nd in n for nd in no_decay)],
             'weight_decay_rate': 0.01},
            {'params': [p for n, p in model.named_parameters()
                        if any(nd in n for nd in no_decay)],
             'weight_decay_rate': 0.0}]
        optimizer = AdamW(
            optimizer_parameters,
            lr=config.lr,
            betas=(0.9, 0.999),
            weight_decay=1e-8,
            correct_bias=False)
    else:  # rnn
        optimizer = Adam(model.parameters(), lr=config.lr)

    criterion = nn.CrossEntropyLoss(reduction='mean', ignore_index=IGNORE_INDEX)  # 交叉熵损失
    binary_criterion = nn.BCEWithLogitsLoss(reduction='mean')  # 二元损失
    sp_loss_fct = nn.BCEWithLogitsLoss(reduction='none')  # 用于sp，平均值自己算

    #load model states.
    # if config.trained_weight:
    #     model.load_state_dict(torch.load(config.trained_weight))
    # model.to(device)
    # if torch.cuda.is_available():
    #     model = model
        # model = torch.nn.parallel.DistributedDataParallel(
        #     model, find_unused_parameters=True)
    # 3. Train
    trainer = Trainer(model=model, data_loader=data_loader,
                      device=device, config=config)


    def train_loop_fn(loader):
        tracker = xm.RateTracker()
        model.train()
        for x, batch in enumerate(loader):
            # batch = tuple(t.to(device) for t in batch)
            # loss = self.criterion(logits, batch[-1])
            start_logits, end_logits, type_logits, sp_logits, start_position, end_position = model(*batch)
            loss1 = criterion(start_logits, batch[-4]) + criterion(end_logits, batch[-3])
            loss2 = config.type_lambda * criterion(type_logits, batch[-2])

            sp_value = sp_loss_fct(sp_logits.view(-1), batch[-1].float().view(-1)).sum()
            sent_num_in_batch = batch[-7].sum()
            loss3 = config.sp_lambda * sp_value / sent_num_in_batch

            loss = loss1 + loss2 + loss3
            loss.backward()

            tracker.add(FLAGS.batch_size)
            if (x + 1) % config.gradient_accumulation_steps == 0:
                torch.nn.utils.clip_grad_norm_(
                    model.parameters(), config.max_grad_norm)
                # after 梯度累加的基本思想在于，在优化器更新参数前，也就是执行 optimizer.step() 前，进行多次反向传播，是的梯度累计值自动保存在 parameter.grad 中，最后使用累加的梯度进行参数更新。
                xm.optimizer_step(optimizer)
                optimizer.zero_grad()

            if xm.get_ordinal() == 0:
                if x % FLAGS.log_steps == 0:
                    print('[xla:{}]({}) Loss={:.5f} Rate={:.2f} GlobalRate={:.2f} Time={}'.format(
                        xm.get_ordinal(), x, loss.item(), tracker.rate(),
                        tracker.global_rate(), time.asctime()), flush=True)

    def test_loop_fn(loader):
        total_samples = 0
        correct = 0
        model.eval()
        data, pred, target = None, None, None
        tracker = xm.RateTracker()
        for x, batch in enumerate(loader):
            output = model(*batch[:-1])  # the last one is label
            target = batch[-1]
            # pred = output.max(1, keepdim=True)[1]
            # correct += pred.eq(target.view_as(pred)).sum().item()
            for i in range(len(output)):
                logits = output[i]
                pred = int(torch.argmax(logits, dim=-1))
                if pred == target[i]:
                    correct += 1
            total_samples += len(output)

            if xm.get_ordinal() == 0:
                if x % FLAGS.log_steps == 0:
                    print('[xla:{}]({}) Acc={:.5f} Rate={:.2f} GlobalRate={:.2f} Time={}'.format(
                        xm.get_ordinal(), x, correct*1.0/total_samples, tracker.rate(),
                        tracker.global_rate(), time.asctime()), flush=True)

        accuracy = 100.0 * correct / total_samples
        if xm.get_ordinal() == 0:
            print('[xla:{}] Accuracy={:.2f}%'.format(xm.get_ordinal(), accuracy), flush=True)
        return accuracy, data, pred, target

    # Train and eval loops
    accuracy = 0.0
    data, pred, target = None, None, None
    for epoch in range(FLAGS.num_epoch):
        para_loader = pl.ParallelLoader(data_loader['train'], [device])
        train_loop_fn(para_loader.per_device_loader(device))
        xm.master_print("Finished training epoch {}".format(epoch))

        # para_loader = pl.ParallelLoader(data_loader['valid_train'], [device])
        # accuracy_train, data, pred, target = test_loop_fn(para_loader.per_device_loader(device))

        # para_loader = pl.ParallelLoader(data_loader['valid_valid'], [device])
        # accuracy_valid, data, pred, target = test_loop_fn(para_loader.per_device_loader(device))
        # xm.master_print("Finished test epoch {}, valid={:.2f}".format(epoch, accuracy_valid))

    # # 4. Save model
    # torch.save(best_model_state_dict,
    #            os.path.join(config.model_path, 'model.bin'))
    if xm.get_ordinal() == 0:
        results = trainer.valid()
        xm.master_print("Finished training epoch {}".format(results))
    #     return best_model_state_dict
    # return {}

def _mp_fn(rank, flags, model,serial):
    global WRAPPED_MODEL, FLAGS, SERIAL_EXEC
    WRAPPED_MODEL = model
    FLAGS = flags
    SERIAL_EXEC = serial
    torch.set_default_tensor_type('torch.FloatTensor')

    main(args.config_file)
    # Retrieve tensors that are on TPU core 0 and plot.
    # plot_results(data.cpu(), pred.cpu(), target.cpu())
    xm.master_print(('DONE', rank))
    # 4. Save model
    if xm.get_ordinal() == 0:
        WRAPPED_MODEL.to('cpu')
        torch.save(WRAPPED_MODEL.state_dict(), os.path.join(config.model_path, 'model.bin'))
        xm.master_print('saved model.')


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-c', '--config_file', default='config/lbert_config.json',
        help='model config file')

    # parser.add_argument(
    #     '--local_rank', default=0,
    #     help='used for distributed parallel')
    args = parser.parse_args()
    with open(args.config_file) as fin:
        config = json.load(fin, object_hook=lambda d: SimpleNamespace(**d))

    # main(args.config_file)
    WRAPPED_MODEL = MODEL_MAP[config.model_type](config)
    if config.trained_weight:
        WRAPPED_MODEL.load_state_dict(torch.load(config.trained_weight))
    FLAGS = config
    SERIAL_EXEC = xmp.MpSerialExecutor()

    # main(args.config_file)
    xmp.spawn(_mp_fn, args=(FLAGS,WRAPPED_MODEL,SERIAL_EXEC, ), nprocs=config.num_cores, start_method='fork')


