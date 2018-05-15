import pandas as pd
from sklearn.model_selection import StratifiedKFold, KFold
from sklearn.feature_extraction.text import CountVectorizer, HashingVectorizer, TfidfTransformer, TfidfVectorizer
from scipy.sparse import csr_matrix, hstack
from sklearn.metrics import mean_squared_error
from sklearn.linear_model import Ridge
from sklearn.feature_selection import SelectKBest
from sklearn.feature_selection import SelectFromModel
from sklearn.feature_selection import chi2
import lightgbm as lgb
import jieba
from snownlp import SnowNLP
import re
import pickle
from sklearn.linear_model import Ridge
import numpy as np
from lightgbm import LGBMRegressor
from xgboost import XGBRegressor
from sklearn.linear_model import LogisticRegression
from scipy.sparse import csr_matrix


def get_data():
    train = pd.read_csv("../../data/train_22w_translate_v11.csv")
    test = pd.read_csv("../../data/test_5w_translate_v11.csv")



    data = pd.concat([train, test])   #train和testconcat起来操作
    print('train %s test %s' % (train.shape, test.shape))
    print('train columns', train.columns)
    return data, train.shape[0], train['Score'], test['Id']

def xx_mse_s(y_true, y_pre):
    y_true = y_true
    y_pre = pd.DataFrame({'res': list(y_pre)})

    y_pre['res'] = y_pre['res'].astype(int)
    return 1 / (1 + mean_squared_error(y_true, y_pre['res'].values) ** 0.5)



#对tf-idf的值做了一定的修改加了类别修正信息，参考论文http://manu44.magtech.com.cn/Jwk_infotech_wk3/article/2015/1003-3513/1003-3513-31-3-39.html
def calc_cd_feature(cv,train):
    c = np.zeros(len(cv.get_feature_names()))
    for i in [5,4,3,2,1]:
        numerator = cv.transform(train[train["Score"].astype(int)==i]["discuss_segment_jieba"])
        a = np.array(numerator.tocsr().sum(axis=0).tolist()[0])
        c += np.where(a > 0, 1, 0)
    return csr_matrix(5/c)


def pre_process(): #简单的特征工程
    data, nrw_train, y, test_id = get_data()
    train=data[:nrw_train]
    cv=CountVectorizer(ngram_range=(1,6),analyzer="char",max_features=10000)
    cv.fit(train["discuss_segment_jieba"])
    cd_feature=calc_cd_feature(cv,train)
    transformer = TfidfTransformer()
    tfidf = transformer.fit_transform(cv.transform(data["discuss_segment_jieba"]))
    discuss_tf=tfidf.multiply(cd_feature)


    cv = CountVectorizer(ngram_range=(1, 3), analyzer="word", max_features=10000)
    cv.fit(train["discuss_segment_jieba"])
    cd_feature = calc_cd_feature(cv, train)
    transformer = TfidfTransformer()
    tfidf = transformer.fit_transform(cv.transform(data["discuss_segment_jieba"]))
    discuss_tf_1 = tfidf.multiply(cd_feature)

    data = hstack((discuss_tf,discuss_tf_1)).tocsr()

    return data[:nrw_train], data[nrw_train:], y, test_id



def round_score(data):
    count = [0, 0, 0, 0, 0]

    def _round_score(score):
        if score > 4.7:
            count[4] += 1
            return 5.0

        if score<1:
            count[0]+=1
            return 1
        return score
    data = data.apply(lambda x: _round_score(x))
    print(count)
    return data




if __name__ == '__main__':
    X, test, y, test_id = pre_process()
    p=(X,test,y,test_id)
    pickle.dump(p,open("./output/data.pkl","wb"))


    train_X,test_X,train_Y,test_id= pickle.load(open("./output/data.pkl","rb"))



    print("X shape:",train_X.shape)
    print("test shape:",test_X.shape)


    #对特征加以选择
    model = LogisticRegression(solver='sag')
    sfm = SelectFromModel(model, )
    train_X = sfm.fit_transform(train_X, train_Y)
    test_X = sfm.transform(test_X)

    p=(train_X,test_X,train_Y,test_id)
    pickle.dump(p,open("./output/data_2.pkl","wb"))
    train_X, test_X, train_Y, test_id = pickle.load(open("./output/data_2.pkl", "rb"))

    print("X shape:", train_X.shape)
    print("test shape:", test_X.shape)


    xgb_params = {
        'booster': 'gbtree',
        'n_estimators': 20000,
        'n_jobs': 36,
        "seed": 1,
        'max_depth': 8,
        'subsample': 0.8,
        'colsample_bytree': 0.9,
        'eta': 0.03,
    }

    kfold = 10
    lgb = XGBRegressor(**xgb_params)
    base_models = (lgb,)


    folds = pickle.load(open('../../data/fold_10_train_220000_test_50000_by_ding_server.pkl', 'rb'))

    S_train = np.zeros((train_X.shape[0], len(base_models)))
    S_test = np.zeros((test_X.shape[0], len(base_models)))

    for i, clf in enumerate(base_models):
        model = str(clf).split('(')[0]
        if len(model) > 40:
            model = str(clf).split('.')[2].split(' ')[0]
        print('Running {}'.format(model))
        X = train_X.copy()
        y = train_Y.copy()
        T = test_X.copy()
        S_test_i = np.zeros((T.shape[0], kfold))
        mse = []
        mse_round = []
        cv_pred = []
        for j, (train_index, test_index) in enumerate(folds):
            X_train, X_eval = X[train_index], X[test_index]
            y_train, y_eval = y[train_index], y[test_index]
            if model in ['LGBMRegressor', 'XGBRegressor',]:
                clf.set_params(random_state=j)
                clf.fit(X_train, y_train, eval_set=[(X_eval, y_eval)], early_stopping_rounds=100, eval_metric='rmse',
                        verbose=50)
            elif model == 'CatBoostRegressor':
                clf.fit(X_train, y_train, eval_set=[X_eval, y_eval], use_best_model=True, verbose=False)
            else:
                clf.fit(X_train, y_train)
            y_pred = clf.predict(X_eval)
            S_train[test_index, i] = y_pred
            S_test_i[:, j] = clf.predict(T)

            cv_pred.append(S_test_i[:, j])

            temp = xx_mse_s(y_eval, y_pred)
            mse.append(temp)
            temp3 = pd.DataFrame({'res': list(y_pred)})
            temp3["res"] = round_score(temp3["res"])
            temp2 = xx_mse_s(y_eval, list(temp3["res"]))
            mse_round.append(temp2)
            print("fold_{}_cv_original_score_{}".format(j+1,temp))
            print("fold_{}_cv_round_score_{}".format(j+1,temp2))


        print('cv_original_result:{}'.format(np.mean(mse)))
        print('cv_round_result:{}'.format(np.mean(mse_round)))
        print("-" * 20)
        S_test[:, i] = S_test_i.mean(axis=1)

        def write():
            s = 0
            for i in cv_pred:
                s = s + i

            s = s / kfold
            res = pd.DataFrame()
            res['Id'] = list(test_id)
            res['pre'] = list(s)
            res.to_csv('./output/XGB_with_Select_K_Best_on_TFIDF_v3.csv', index=False, header=False)
            res["pre"] = round_score(res["pre"])
            #修改阈值的
            res.to_csv('./output/XGB_with_Select_K_Best_on_TFIDF_round_score_v3.csv', index=False, header=False)


        write()
    ## 保存元特征
    ## S_train_v1的维度要和S_test_v1的一致
    pickle.dump(S_train, open('./output/S_train_XGB_with_Select_K_Best_on_TFIDF_stacking_v3.pkl', 'wb'))
    pickle.dump(S_test, open('./output/S_test_XGB_with_Select_K_Best_on_TFIDF_stacking_v3.pkl', 'wb'))
    print("X shape:", X.shape)

