# Getting mu and sigma of activation features of GCNN classifier for the FID score

import torch
import torch.nn.functional as F

import torch_geometric.transforms as T

from torch_geometric.utils import normalized_cut
from torch_geometric.nn import (graclus, max_pool, global_mean_pool)
from torch_geometric.nn import GMMConv

from tqdm import tqdm

import numpy as np

import utils

from os import path

from scipy.spatial.distance import jensenshannon
from scipy.stats import wasserstein_distance

from skhep.math.vectors import LorentzVector

cutoff = 0.32178


def normalized_cut_2d(edge_index, pos):
    row, col = edge_index
    edge_attr = torch.norm(pos[row] - pos[col], p=2, dim=1)
    return normalized_cut(edge_index, edge_attr, num_nodes=pos.size(0))


class MoNet(torch.nn.Module):
    def __init__(self, kernel_size):
        super(MoNet, self).__init__()
        self.conv1 = GMMConv(1, 32, dim=2, kernel_size=kernel_size)
        self.conv2 = GMMConv(32, 64, dim=2, kernel_size=kernel_size)
        self.conv3 = GMMConv(64, 64, dim=2, kernel_size=kernel_size)
        self.fc1 = torch.nn.Linear(64, 128)
        self.fc2 = torch.nn.Linear(128, 10)

    def forward(self, data):
        row, col = data.edge_index
        data.edge_attr = (data.pos[col] - data.pos[row]) / (2 * 28 * cutoff) + 0.5

        data.x = F.elu(self.conv1(data.x, data.edge_index, data.edge_attr))
        weight = normalized_cut_2d(data.edge_index, data.pos)
        cluster = graclus(data.edge_index, weight, data.x.size(0))
        data.edge_attr = None
        data = max_pool(cluster, data, transform=T.Cartesian(cat=False))

        row, col = data.edge_index
        data.edge_attr = (data.pos[col] - data.pos[row]) / (2 * 28 * cutoff) + 0.5

        data.x = F.elu(self.conv2(data.x, data.edge_index, data.edge_attr))
        weight = normalized_cut_2d(data.edge_index, data.pos)
        cluster = graclus(data.edge_index, weight, data.x.size(0))
        data = max_pool(cluster, data, transform=T.Cartesian(cat=False))

        row, col = data.edge_index
        data.edge_attr = (data.pos[col] - data.pos[row]) / (2 * 28 * cutoff) + 0.5

        data.x = F.elu(self.conv3(data.x, data.edge_index, data.edge_attr))

        x = global_mean_pool(data.x, data.batch)
        return self.fc1(x)

        x = F.elu(self.fc1(x))
        x = F.dropout(x, training=self.training)
        return F.log_softmax(self.fc2(x), dim=1)


def get_mu2_sigma2(args, C, X_loaded, fullpath):
    print("getting mu2, sigma2")
    activations = 0
    for batch_ndx, data in tqdm(enumerate(X_loaded), total=len(X_loaded)):
        tg_data = utils.tg_transform(args, data.to(args.device))
        if(batch_ndx % args.gpu_batch == 0):
            if(batch_ndx == args.gpu_batch):
                np_activations = activations.cpu().detach().numpy()
            elif(batch_ndx > args.gpu_batch):
                np_activations = np.concatenate((np_activations, activations.cpu().detach().numpy()))
            activations = C(tg_data)
        else:
            activations = torch.cat((C(tg_data), activations), axis=0)
        # if batch_ndx == 113:
        #     break

    activations = np.concatenate((np_activations, activations.cpu().detach().numpy()))  # because torch doesn't have a built in function for calculating the covariance matrix

    print(activations.shape)

    mu = np.mean(activations, axis=0)
    sigma = np.cov(activations, rowvar=False)

    np.savetxt(fullpath + "mu2.txt", mu)
    np.savetxt(fullpath + "sigma2.txt", sigma)

    return mu, sigma


def load(args, X_loaded):
    C = MoNet(25).to(args.device)
    C.load_state_dict(torch.load(args.eval_path + "C_state_dict.pt"))
    numstr = str(args.num) if args.num != -1 else "all_nums"
    dstr = "_sm_nh_" + str(args.num_hits) + "_" if args.sparse_mnist else "_sp_"
    fullpath = args.eval_path + numstr + dstr
    print(fullpath)
    if path.exists(fullpath + "mu2.txt"):
        mu2 = np.loadtxt(fullpath + "mu2.txt")
        sigma2 = np.loadtxt(fullpath + "sigma2.txt")
    else:
        mu2, sigma2 = get_mu2_sigma2(args, C, X_loaded, fullpath)
    return (C, mu2, sigma2)


# make sure to deepcopy G passing in
def get_fid(args, C, G, dist, mu2, sigma2):
    print("evaluating fid")
    G.eval()
    C.eval()
    num_iters = np.ceil(float(args.fid_eval_size) / float(args.fid_batch_size))
    with torch.no_grad():
        for i in tqdm(range(int(num_iters))):
            gen_data = utils.tg_transform(args, utils.gen(args, G, dist, args.fid_batch_size))
            if(i == 0):
                activations = C(gen_data)
            else:
                activations = torch.cat((C(gen_data), activations), axis=0)

    activations = activations.cpu().detach().numpy()

    mu1 = np.mean(activations, axis=0)
    sigma1 = np.cov(activations, rowvar=False)

    fid = utils.calculate_frechet_distance(mu1, sigma1, mu2, sigma2)
    print("fid:" + str(fid))

    return fid


rng = np.random.default_rng()


# make sure to deepcopy G passing in
def calc_jsd(args, X, G, dist):
    print("evaluating JSD")
    G.eval()

    bins = [np.arange(-1, 1, 0.02), np.arange(-1, 1, 0.02), np.arange(-1, 1, 0.01)]
    N = len(X)

    jsds = []

    for j in tqdm(range(10)):
        gen_out = utils.gen(args, G, dist=dist, num_samples=args.batch_size).cpu().detach().numpy()
        for i in range(int(args.num_samples / args.batch_size)):
            gen_out = np.concatenate((gen_out, utils.gen(args, G, dist=dist, num_samples=args.batch_size).cpu().detach().numpy()), 0)
        gen_out = gen_out[:args.num_samples]

        sample = X[rng.choice(N, size=args.num_samples, replace=False)].cpu().detach().numpy()
        jsd = []

        for i in range(3):
            hist1 = np.histogram(gen_out[:, :, i].reshape(-1), bins=bins[i], density=True)[0]
            hist2 = np.histogram(sample[:, :, i].reshape(-1), bins=bins[i], density=True)[0]
            jsd.append(jensenshannon(hist1, hist2))

        jsds.append(jsd)

    return np.mean(np.array(jsds), axis=0), np.std(np.array(jsds), axis=0)


# make sure to deepcopy G passing in
def calc_w1(args, X, G, dist, losses, X_loaded=None):
    print("evaluating 1-WD")
    num_batches = np.array(100000 / np.array(args.w1_num_samples), dtype=int)
    # num_batches = [5, 5, 5]
    G.eval()

    N = len(X)

    for k in range(len(args.w1_num_samples)):
        print("Num Samples: " + str(args.w1_num_samples[k]))
        w1s = []
        if args.jf: w1js = []
        for j in tqdm(range(num_batches[k])):
            gen_out = utils.gen(args, G, dist=dist, num_samples=args.batch_size, X_loaded=X_loaded).cpu().detach().numpy()
            for i in range(int(args.w1_num_samples[k] / args.batch_size)):
                gen_out = np.concatenate((gen_out, utils.gen(args, G, dist=dist, num_samples=args.batch_size, X_loaded=X_loaded).cpu().detach().numpy()), 0)
            gen_out = gen_out[:args.w1_num_samples[k]]

            sample = X[rng.choice(N, size=args.w1_num_samples[k])].cpu().detach().numpy()
            w1 = []

            for i in range(3):
                w1.append(wasserstein_distance(sample[:, :, i].reshape(-1), gen_out[:, :, i].reshape(-1)))

            w1s.append(w1)

            if args.jf:
                realj = []
                genj = []

                for i in range(args.w1_num_samples[k]):
                    jetv = LorentzVector()

                    for part in sample[i]:
                        vec = LorentzVector()
                        vec.setptetaphim(part[2], part[0], part[1], 0)
                        jetv += vec

                    realj.append([jetv.mass, jetv.pt])

                for i in range(args.w1_num_samples[k]):
                    jetv = LorentzVector()

                    for part in gen_out[i]:
                        vec = LorentzVector()
                        vec.setptetaphim(part[2], part[0], part[1], 0)
                        jetv += vec

                    genj.append([jetv.mass, jetv.pt])

                w1j = []
                for i in range(len(args.jet_features)):
                    w1j.append(wasserstein_distance(np.array(realj)[:, i], np.array(genj)[:, i]))

                w1js.append(w1j)

        losses['w1_' + str(args.w1_num_samples[k]) + 'm'].append(np.mean(np.array(w1s), axis=0))
        losses['w1_' + str(args.w1_num_samples[k]) + 'std'].append(np.std(np.array(w1s), axis=0))

        if args.jf:
            losses['w1j_' + str(args.w1_num_samples[k]) + 'm'].append(np.mean(np.array(w1js), axis=0))
            losses['w1j_' + str(args.w1_num_samples[k]) + 'std'].append(np.std(np.array(w1js), axis=0))
