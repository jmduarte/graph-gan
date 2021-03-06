import torch
import torch.nn as nn
import torch.nn.functional as F

from spectral_normalization import SpectralNorm


class Graph_GAN(nn.Module):
    def __init__(self, gen, args):
        super(Graph_GAN, self).__init__()
        self.args = args

        self.G = gen
        self.D = not gen

        self.args.spectral_norm = self.args.spectral_norm_gen if self.G else self.args.spectral_norm_disc
        self.args.batch_norm = self.args.batch_norm_gen if self.G else self.args.batch_norm_disc
        self.args.mp_iters = self.args.mp_iters_gen if self.G else self.args.mp_iters_disc
        self.args.fe1 = self.args.fe1g if self.G else self.args.fe1d
        if self.G: self.args.dea = False

        if not self.args.fe1: self.args.fe1 = self.args.fe.copy()
        self.args.fn1 = self.args.fn.copy()

        anc = 0
        if self.args.pos_diffs:
            if self.args.deltacoords:
                if self.args.coords == 'cartesian':
                    anc += 3
                elif self.args.coords == 'polar' or self.args.coords == 'polarrel':
                    anc += 2
            if self.args.deltar:
                anc += 1

        if self.args.mask:
            anc += 1

        anc += int(self.args.int_diffs)
        self.args.fe_in_size = 2 * self.args.hidden_node_size + anc + self.args.clabels_hidden_layers
        self.args.fe_out_size = self.args.fe[-1]
        self.args.fe.insert(0, self.args.fe_in_size)

        # self.args.hidden_node_size += self.args.clabels_hidden_layers
        self.args.fn.insert(0, self.args.fe_out_size + self.args.hidden_node_size + self.args.clabels_hidden_layers)
        self.args.fn.append(self.args.hidden_node_size)

        if(self.args.dea):
            self.args.fnd.insert(0, self.args.hidden_node_size)
            self.args.fnd.append(1)

        self.fe = nn.ModuleList()
        self.fn = nn.ModuleList()

        if(self.args.batch_norm):
            self.bne = nn.ModuleList()
            self.bnn = nn.ModuleList()

        if self.G:
            first_layer_node_size = self.args.latent_node_size if self.args.latent_node_size else self.args.hidden_node_size
        else:
            first_layer_node_size = self.args.node_feat_size

        self.args.fe1_in_size = 2 * first_layer_node_size + anc + self.args.clabels_first_layer
        self.args.fe1.insert(0, self.args.fe1_in_size)
        self.args.fe1_out_size = self.args.fe1[-1]
        fe_iter = nn.ModuleList()
        if self.args.batch_norm: bne = nn.ModuleList()
        for j in range(len(self.args.fe1) - 1):
            linear = nn.Linear(self.args.fe1[j], self.args.fe1[j + 1])
            fe_iter.append(linear)
            if self.args.batch_norm: bne.append(nn.BatchNorm1d(self.args.fe1[j + 1]))

        self.fe.append(fe_iter)
        if self.args.batch_norm: self.bne.append(bne)

        # first_layer_node_size += self.args.clabels_first_layer
        self.args.fn1.insert(0, self.args.fe1_out_size + first_layer_node_size + self.args.clabels_first_layer)
        self.args.fn1.append(self.args.hidden_node_size)

        # node network
        fn_iter = nn.ModuleList()
        if self.args.batch_norm: bnn = nn.ModuleList()
        for j in range(len(self.args.fn1) - 1):
            linear = nn.Linear(self.args.fn1[j], self.args.fn1[j + 1])
            fn_iter.append(linear)
            if self.args.batch_norm: bnn.append(nn.BatchNorm1d(self.args.fn1[j + 1]))

        self.fn.append(fn_iter)
        if self.args.batch_norm: self.bnn.append(bnn)

        # if self.D or self.args.latent_node_size:
        #     self.args.fe1_in_size = 2 * self.args.latent_node_size if self.G else 2 * self.args.node_feat_size
        #     self.args.fe1_in_size += anc
        #     self.args.fe1.insert(0, self.args.fe1_in_size)
        #     self.args.fe1_out_size = self.args.fe1[-1]
        #     fe_iter = nn.ModuleList()
        #     if self.args.batch_norm: bne = nn.ModuleList()
        #     for j in range(len(self.args.fe1) - 1):
        #         linear = nn.Linear(self.args.fe1[j], self.args.fe1[j + 1])
        #         fe_iter.append(linear)
        #         if self.args.batch_norm: bne.append(nn.BatchNorm1d(self.args.fe1[j + 1]))
        #
        #     self.fe.append(fe_iter)
        #     if self.args.batch_norm: self.bne.append(bne)
        #
        #     node_size = self.args.latent_node_size if self.G else self.args.node_feat_size
        #     self.args.fn1.insert(0, self.args.fe1_out_size + node_size)
        #     self.args.fn1.append(self.args.hidden_node_size)
        #
        #     # node network
        #     fn_iter = nn.ModuleList()
        #     if self.args.batch_norm: bnn = nn.ModuleList()
        #     for j in range(len(self.args.fn1) - 1):
        #         linear = nn.Linear(self.args.fn1[j], self.args.fn1[j + 1])
        #         fn_iter.append(linear)
        #         if self.args.batch_norm: bnn.append(nn.BatchNorm1d(self.args.fn1[j + 1]))
        #
        #     self.fn.append(fn_iter)
        #     if self.args.batch_norm: self.bnn.append(bnn)
        # else:
        #     self.args.fe1_in_size = self.args.fe_in_size
        #     self.args.fe1_out_size = self.args.fe_out_size

        for i in range(self.args.mp_iters - 1):
            # edge network
            fe_iter = nn.ModuleList()
            if self.args.batch_norm: bne = nn.ModuleList()
            for j in range(len(self.args.fe) - 1):
                linear = nn.Linear(self.args.fe[j], self.args.fe[j + 1])
                fe_iter.append(linear)
                if self.args.batch_norm: bne.append(nn.BatchNorm1d(self.args.fe[j + 1]))

            self.fe.append(fe_iter)
            if self.args.batch_norm: self.bne.append(bne)

            # node network
            fn_iter = nn.ModuleList()
            if self.args.batch_norm: bnn = nn.ModuleList()
            for j in range(len(self.args.fn) - 1):
                linear = nn.Linear(self.args.fn[j], self.args.fn[j + 1])
                fn_iter.append(linear)
                if self.args.batch_norm: bnn.append(nn.BatchNorm1d(self.args.fn[j + 1]))

            self.fn.append(fn_iter)
            if self.args.batch_norm: self.bnn.append(bnn)

        if(self.args.dea):
            self.fnd = nn.ModuleList()
            self.bnd = nn.ModuleList()
            for i in range(len(self.args.fnd) - 1):
                linear = nn.Linear(self.args.fnd[i], self.args.fnd[i + 1])
                self.fnd.append(linear)
                if self.args.batch_norm: self.bnd.append(nn.BatchNorm1d(self.args.fnd[i + 1]))

        p = self.args.gen_dropout if self.G else self.args.disc_dropout
        self.dropout = nn.Dropout(p=p)

        if self.args.glorot: self.init_params()

        if self.args.spectral_norm:
            for ml in self.fe:
                for i in range(len(ml)):
                    ml[i] = SpectralNorm(ml[i])

            for ml in self.fn:
                for i in range(len(ml)):
                    ml[i] = SpectralNorm(ml[i])

            if self.args.dea:
                for i in range(len(self.fnd)):
                    self.fnd[i] = SpectralNorm(self.fnd[i])

        print("fe: ")
        print(self.fe)

        print("fn: ")
        print(self.fn)

        if(self.args.dea):
            print("fnd: ")
            print(self.fnd)

    def forward(self, x, labels=None, deb=False):
        batch_size = x.shape[0]
        if self.args.mask_weights and self.D:
            mask = x[:, :, self.args.node_feat_size - 1:self.args.node_feat_size] + 0.5

        for i in range(self.args.mp_iters):
            # print(i)
            clabel_iter = self.args.clabels and ((i == 0 and self.args.clabels_first_layer) or (i and self.args.clabels_hidden_layers))

            node_size = x.size(2)
            fe_in_size = self.args.fe_in_size if i else self.args.fe1_in_size
            fe_out_size = self.args.fe_out_size if i else self.args.fe1_out_size

            if clabel_iter: fe_in_size -= self.args.clabels

            # message passing
            A = self.getA(x, batch_size, fe_in_size)

            if clabel_iter: A = torch.cat((A, labels.repeat(self.args.num_hits ** 2, 1)), axis=1)

            for j in range(len(self.fe[i])):
                A = F.leaky_relu(self.fe[i][j](A), negative_slope=self.args.leaky_relu_alpha)
                if(self.args.batch_norm): A = self.bne[i][j](A)  # try before activation
                A = self.dropout(A)

            # message aggregation into new features
            A = A.view(batch_size, self.args.num_hits, self.args.num_hits, fe_out_size)
            A = torch.sum(A, 2) if self.args.sum else torch.mean(A, 2)
            x = torch.cat((A, x), 2).view(batch_size * self.args.num_hits, fe_out_size + node_size)

            if clabel_iter: x = torch.cat((x, labels.repeat(self.args.num_hits, 1)), axis=1)

            for j in range(len(self.fn[i]) - 1):
                x = F.leaky_relu(self.fn[i][j](x), negative_slope=self.args.leaky_relu_alpha)
                if(self.args.batch_norm): x = self.bnn[i][j](x)
                x = self.dropout(x)

            x = self.dropout(self.fn[i][-1](x))
            x = x.view(batch_size, self.args.num_hits, self.args.hidden_node_size)

        # if deb: print(x[:10, :, 0])

        if(self.G):
            # if(self.args.coords == 'polarrel' or self.args.coords == 'polarrelabspt'):
            #     x = torch.cat((x[:, :, :2], torch.relu(x[:, :, 2].unsqueeze(-1))), axis=2)
            return torch.tanh(x[:, :, :self.args.node_feat_size]) if self.args.gtanh else x[:, :, :self.args.node_feat_size]
        else:
            if(self.args.dea):
                x = torch.sum(x, 1) if self.args.sum else torch.mean(x, 1)
                for i in range(len(self.fnd) - 1):
                    x = F.leaky_relu(self.fnd[i](x), negative_slope=self.args.leaky_relu_alpha)
                    if(self.args.batch_norm): x = self.bnd[i](x)
                    x = self.dropout(x)
                x = self.dropout(self.fnd[-1](x))
            else:
                if self.args.mask_weights: x = x[:, :, :1] * mask
                else: x = x[:, :, :1]

                x = torch.sum(x, 1) if (self.args.loss == 'w' or self.args.loss == 'hinge' or not self.args.dearlysigmoid) else torch.sum(torch.sigmoid(x), 1)
                x = x / torch.sum(mask, 1) if self.args.mask_weights else x / self.args.num_hits

            # if self.args.debug: print(x[0, :10, 0])
            return x if (self.args.loss == 'w' or self.args.loss == 'hinge') else torch.sigmoid(x)

    def getA(self, x, batch_size, fe_in_size):
        node_size = x.size(2)
        x1 = x.repeat(1, 1, self.args.num_hits).view(batch_size, self.args.num_hits * self.args.num_hits, node_size)
        x2 = x.repeat(1, self.args.num_hits, 1)

        # print(x.shape)

        if(self.args.pos_diffs):
            num_coords = 3 if self.args.coords == 'cartesian' else 2
            diffs = x2[:, :, :num_coords] - x1[:, :, :num_coords]
            dists = torch.norm(diffs + 1e-12, dim=2).unsqueeze(2)

            if self.args.deltar and self.args.deltacoords:
                A = torch.cat((x1, x2, diffs, dists), 2)
            elif self.args.deltar:
                A = torch.cat((x1, x2, dists), 2)
            elif self.args.deltacoords:
                A = torch.cat((x1, x2, diffs), 2)

            if(self.args.mask):
                A = torch.cat((A, x2[:, :, 3].unsqueeze(2)), 2)

            A = A.view(batch_size * self.args.num_hits * self.args.num_hits, fe_in_size)
        else:
            A = torch.cat((x1, x2), 2).view(batch_size * self.args.num_hits * self.args.num_hits, fe_in_size)

        return A

    def init_params(self):
        print("glorot-ing")
        for m in self.modules():
            if isinstance(m, nn.Linear):
                torch.nn.init.xavier_uniform(m.weight, self.args.glorot)

    def load(self, backup):
        for m_from, m_to in zip(backup.modules(), self.modules()):
            if isinstance(m_to, nn.Linear):
                m_to.weight.data = m_from.weight.data.clone()
                if m_to.bias is not None:
                    m_to.bias.data = m_from.bias.data.clone()

    def reset_params(self):
        for m in self.modules():
            if isinstance(m, nn.Linear):
                m.reset_parameters()
