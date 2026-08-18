# 公开数据集（回放验证用，不入库）

本目录存放 `scripts/dataset_replay.py` 回放所用的公开数据集原始文件。
数据文件体积大（~144MB）且可从公开渠道随时获取，**不提交进 git**（.gitignore 已排除）。

## creditcard.csv（ULB Credit Card Fraud Detection）

- 内容：2013-09 欧洲持卡人两天 284,807 笔信用卡交易，492 笔欺诈（0.172%），
  PCA 匿名特征 V1-V28 + Time + Amount + Class；
- 提供者：Andrea Dal Pozzolo 等（ULB Machine Learning Group & Worldline）；
- 存档：Zenodo DOI [10.5281/zenodo.7395559](https://doi.org/10.5281/zenodo.7395559)
  （存档声明 CC-BY-4.0）；
- 原始：Kaggle [mlg-ulb/creditcardfraud](https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud)
  （DbCL 许可，学术引用请注明提供者）；
- 完整性：md5 `e90efcb83d69faf99fcab8b0255024de`。

下载（任一渠道）：

```powershell
# Zenodo（学术存档，含 md5 可校验）
curl.exe -L -o creditcard.csv `
  "https://zenodo.org/api/records/7395559/files/creditcard.csv/content"

# 校验
Get-FileHash -Algorithm MD5 creditcard.csv
```

回放与报告生成：

```powershell
c:\MyGit\TradeGuard\.venv\Scripts\python.exe scripts\dataset_replay.py
# 输出：docs/reports/dataset-replay.md
```
