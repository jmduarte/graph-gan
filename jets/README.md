# Generating Jets



1) Download the dataset from https://zenodo.org/record/3601436

2) Preprocess into our data format using [preprocessing.py](jets/preprocessing.py)

3) Run [main.py](jets/main.py) with the default parameters to start training.

Figures (particle, jet level feature distributions) and models will be saved every five epochs in the figs and models directories. 1-Wasserstein scores and losses will be saved in the losses directory.
