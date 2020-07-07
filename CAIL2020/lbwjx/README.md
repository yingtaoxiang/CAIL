# SMP-CAIL2020：论辩挖掘

本项目为 **中国法研杯司法人工智能挑战赛（CAIL2020）** 论辩挖掘赛道参考代码和模型。

主要包含两个基线模型：BERT和RNN。

### 0. 预处理

#### 0.0 下载本项目

```
git clone https://github.com/gaoyixu/CAIL2020-Argument-Mining.git
```

#### 0.1 数据集

数据集下载请访问比赛[主页](http://cail.cipsc.org.cn/)。

本项目中只使用了

`SMP-CAIL2020-train.csv`： 包含了2449对裁判文书中的互动论点对。分别包含以下维度：

  - `id`： 论点对id
  - `text_id`： 裁判文书id
  - `sc`： 论点对中诉方论点
  - `A/B/C/D/E`： 给出的五句候选辩方论点
  - `answer`： 辩方正确论点

划分训练集、验证集：

```
python prepare.py
```

#### 0.2 下载BERT模型（pytorch版本）

下载中文预训练BERT模型存放于`model/bert`和`model/bert/bert-base-chinese`目录

中文预训练BERT模型包含三个文件：

1. [`config.json`](https://s3.amazonaws.com/models.huggingface.co/bert/bert-base-chinese-config.json) 
2. [`pytorch_model.bin`](https://cdn.huggingface.co/bert-base-chinese-pytorch_model.bin)
3. [`vocab.txt`](https://s3.amazonaws.com/models.huggingface.co/bert/bert-base-chinese-vocab.txt) 

初始文件目录：

```
├── config
│   ├── bert_config.json
│   └── rnn_config.json
├── data
│   ├── SMP-CAIL2020-train.csv
│   ├── train.csv
│   └── valid.csv
├── model
│   ├── bert
│   │   ├── bert-base-chinese
│   │   │   ├── config.json
│   │   │   └── pytorch_model.bin
│   │   └── vocab.txt
│   └── rnn
├── __init__.py
├── data.py
├── evaluate.py
├── main.py
├── model.py
├── prepare.py
├── result.py
├── test.py
├── train.py
├── utils.py
└── vocab.py
```

### 1. 训练

#### 1.1 BERT训练

采用4张1080Ti训练，训练参数可在`config/bert_config.json`中调整。

```
python -m torch.distributed.launch train.py --config_file 'config/bert_config.json'
```

<div align = "center">
  <img src="images/bert_train.png" width = "50%"/>
</div>

#### 1.2 RNN训练

采用1张1080Ti训练，训练参数可在`config/rnn_config.json`中调整。

```
CUDA_VISIBLE_DEVICES=0 python -m torch.distributed.launch train.py --config_file 'config/rnn_config.json'
```

<div align = center>
  <img src="images/rnn_train.png" width = "50%"/>
</div>

#### 1.3 训练成果

训练完成后文件目录：

`config`中包含模型训练参数。

`log`中包含模型每个epoch的Accuracy，F1 Score和每步loss的记录数据。

`model`中包含每个epoch训练后的模型和验证集上F1 Score最高的模型`model.bin`。

```
├── config
│   ├── bert_config.json
│   └── rnn_config.json
├── data
│   ├── SMP-CAIL2020-train.csv
│   ├── train.csv
│   └── valid.csv
├── log
│   ├── BERT-epoch.csv
│   ├── BERT-step.csv
│   ├── RNN-epoch.csv
│   └── RNN-step.csv
├── model
│   ├── bert
│   │   ├── BERT
│   │   │   ├── bert-1.bin
│   │   │   ├── bert-2.bin
│   │   │   ├── bert-3.bin
│   │   │   ├── bert-4.bin
│   │   │   ├── bert-5.bin
│   │   │   ├── bert-6.bin
│   │   │   ├── bert-7.bin
│   │   │   ├── bert-8.bin
│   │   │   ├── bert-9.bin
│   │   │   └── bert-10.bin
│   │   ├── bert-base-chinese
│   │   │   ├── config.json
│   │   │   └── pytorch_model.bin
│   │   └── vocab.txt
│   └── rnn
│       ├── model.bin
│       ├── RNN
│       │   ├── rnn-1.bin
│       │   ├── rnn-2.bin
│       │   ├── rnn-3.bin
│       │   ├── rnn-4.bin
│       │   ├── rnn-5.bin
│       │   ├── rnn-6.bin
│       │   ├── rnn-7.bin
│       │   ├── rnn-8.bin
│       │   ├── rnn-9.bin
│       │   └── rnn-10.bin
│       └── vocab.txt
├── __init__.py
├── data.py
├── evaluate.py
├── main.py
├── model.py
├── prepare.py
├── result.py
├── test.py
├── train.py
├── utils.py
└── vocab.py
```

### 2. 测试

`in_file`为待测试文件，`out_file`为输出文件。

#### 2.1 BERT测试

```
python main.py --model_config 'config/bert_config.json' \
               --in_file 'SMP-CAIL2020-test1.csv' \
               --out_file 'bert-submission-test-1.csv'
```

#### 2.2 RNN测试

```
python main.py --model_config 'config/rnn_config.json' \
               --in_file 'data/SMP-CAIL2020-test1.csv' \
               --out_file 'rnn-submission-test-1.csv'
```

### RESULT
202000619,   BERT model,    TEST result: 0.67-0.682
Epoch: 1, train_acc: 0.726073, train_f1: 0.725676, valid_acc: 0.760000, valid_f1: 0.768889,

202000622,   BERT+1-7CNN,   TEST result: 0.696
1,0.7909350755410371,0.790836697236448,0.838,0.8352161478089404
2,0.7913434054716211,0.7912636458834423,0.838,0.8352161478089404

202000622,   BERT+1-14CNN,   TEST result: 0.669 (1 epoch)
train_acc: 0.770110, train_f1: 0.769986, valid_acc: 0.788000, valid_f1: 0.787216,

202000622,   BERT+1-14CNN,   TEST result: 0.669  -  0.75 (3 epoch)
1,0.8836259697835851,0.883557016059752,0.918,0.9195225562637497
2,0.8962841976316864,0.8963458000980952,0.946,0.9452992509238977
3,0.9309922417313189,0.9310764659907621,0.972,0.971331180849851   TEST:0.75
4,0.9403838301347489,0.940379133280375,0.978,0.9778776608845254


202000702,   BERT+1-14CNN, remove BN layer, and dropout rate lowered to 0.5, TEST result: 0.76 (36 epoch)
LESSON: increase max seq len for bert larger than 512 is not permitted. ATTENTION PLEASE.
lr=5E-5, batchsize=8*16
1,0.19466444351813256,0.19429624669578355,0.26,0.26576735957540915
2,0.2888703626511046,0.28845230435379116,0.32,0.3147754756423487
3,0.40308461859107964,0.4028304512571859,0.4,0.3961164439425309
4,0.5027094622759483,0.5023620719525639,0.5,0.49641131815044864
5,0.5781575656523551,0.5778231903273635,0.54,0.5336027280477408
6,0.6360983743226344,0.6357987348426034,0.62,0.6171844745643372
7,0.673197165485619,0.6730942351619704,0.64,0.6381927876045523
8,0.7048770320967069,0.7049225634066703,0.66,0.6569735720375107
9,0.7290537724051688,0.7291748716707211,0.7,0.691759221170986
10,0.7511463109629012,0.751070733349073,0.68,0.6739383753501401
11,0.7790746144226761,0.7790708025973181,0.68,0.6737142857142857
12,0.7969987494789496,0.7969623086970478,0.74,0.7412089314194577
13,0.814922884535223,0.8149808553531951,0.86,0.8621255060728744
14,0.8286786160900376,0.8285628776816198,0.88,0.8837818528221005
15,0.8470195914964569,0.8468518027802423,0.88,0.8837818528221005
16,0.8603584827011255,0.8602378995794553,0.88,0.8837818528221005
17,0.8666110879533139,0.866430748601152,0.9,0.9040935672514621
18,0.8791162984576907,0.8790338511911238,0.92,0.9225101214574899
19,0.8803668195081283,0.8801598203940261,0.92,0.9225101214574899
20,0.8895373072113381,0.8894777639609716,0.92,0.9225101214574899
21,0.8920383493122134,0.8919318041156004,0.94,0.9402539682539682
22,0.8941225510629429,0.894070918956321,0.94,0.9402539682539682
23,0.8978741142142559,0.8979233290042776,0.94,0.9402539682539682
24,0.8982909545644019,0.8983381455492319,0.94,0.9402539682539682
25,0.9024593580658608,0.902539451964919,0.94,0.9402539682539682
26,0.9082951229679033,0.9083138038273033,0.94,0.9402539682539682
27,0.9112130054189246,0.9113642468937309,0.94,0.9402539682539682
28,0.9112130054189246,0.9112481860954537,0.94,0.9402539682539682
29,0.9124635264693622,0.9126028733449327,0.94,0.9402539682539682
30,0.9170487703209671,0.9171683147990564,0.94,0.9402539682539682
31,0.9203834931221342,0.920492634737575,0.94,0.9402539682539682
32,0.921634014172572,0.9217706714822651,0.94,0.9402539682539682
33,0.9208003334722801,0.920991127888762,0.94,0.9402539682539682
34,0.921634014172572,0.9217774062223458,0.94,0.9402539682539682
35,0.9220508545227178,0.9221707466932532,0.94,0.9402539682539682
36,0.9233013755731555,0.9233631511167003,0.94,0.9402539682539682


#20200706  BERT+1-14CNN, remove BN layer, dropout rate = 0.5, 
19584 filter lines with similar sequence then to 18080. 1504 filtered