import json
import os

import numpy
import thulac
import psutil
from sklearn.feature_extraction.text import TfidfVectorizer as TFIDF
import joblib
from sklearn.ensemble import GradientBoostingClassifier
import re

#training score : 0.722

class SingleMulti():
    def __init__(self, tfidf='statement_tfidf.model', gbt='statement_som_gbt.model'):
        print('train tfidf...', self.print_mem())
        self.tfidf = joblib.load(tfidf)
        print('train gbt...', self.print_mem())
        self.gbt = joblib.load(gbt)
        self.cut = thulac.thulac(seg_only=True)

    def cut_text(self, alltext):
        count = 0
        train_text = []
        for text in alltext:
            count += 1
            if count % 2000 == 0:
                print(count)
            train_text.append(self.cut.cut(text, text=True))

        return train_text

    def print_mem(self):
        process = psutil.Process(os.getpid())  # os.getpid()
        memInfo = process.memory_info()
        return '{:.4f}G'.format(1.0 * memInfo.rss / 1024 /1024 /1024)


    def checkSingleMulti(self, statement_option):
        spattern = '下列.*[何|哪|那](一|个).*'
        mpattern = '下列.*[何|哪|那](几|些).*'
        spr = re.compile(spattern)
        mpr = re.compile(mpattern)
        if spr.search(statement_option):
            return 0
        if mpr.search(statement_option):
            return 1
        train_data = self.cut_text([statement_option])
        vec = self.tfidf.transform(train_data)
        yp = self.gbt.predict(vec)
        return yp[0]

    def checkSingleMulti_old(self, statement):
        spattern = '[何|哪|那][一|个|项|种|者]|\(\)|如何|何罪|什么|谁|怎[么|样]'
        mpattern = '[何|哪|那][些]|[几][个|项|种|者]'
        spr = re.compile(spattern)
        mpr = re.compile(mpattern)
        if spr.search(statement):
            return 0
        if mpr.search(statement):
            return 1
        train_data = self.cut_text([statement])
        vec = self.tfidf.transform(train_data)
        yp = self.gbt.predict(vec)
        return yp[0]