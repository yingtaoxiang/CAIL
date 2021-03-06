from model.qa.Bert import BertQA, BertXQA
from model.qa.BertCNN import BertQACNN
from model.qa.BertCNNGRU import BertQACNNGRU
from model.qa.BertCNNGRUGAN import BertQACNNGRUGAN
from model.qa.BiDAF import BiDAFQA
from model.qa.CoMatch import CoMatching
from model.qa.HAF import HAF

model_list = {
    "Bert": BertXQA,
    "BertCNN": BertQACNN,
    "BiDAF": BiDAFQA,
    "Comatch": CoMatching,
    "HAF": HAF
}


def get_model(model_name):
    if model_name in model_list.keys():
        return model_list[model_name]
    else:
        raise NotImplementedError
