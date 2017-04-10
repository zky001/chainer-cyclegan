import numpy as np
import chainer
import chainer.functions as F
import chainer.links as L
import chainer.datasets.image_dataset as ImageDataset
import six
import os

from chainer import cuda, optimizers, serializers, Variable
from chainer import training

def cal_l2_sum(h, t):
    return F.sum((h-t)**2)/ np.prod(h.data.shape)

def loss_func_rec_l1(x_out, t):
    return F.mean_absolute_error(x_out, t)

def loss_func_rec_l2(x_out, t):
    return F.mean_squared_error(x_out, t)

def loss_func_adv_dis_fake(y_fake):
    return cal_l2_sum(y_fake, 0)
    #return F.sum(F.softplus(-y_fake)) / y_fake.data.shape[0]

def loss_func_adv_dis_real(y_real):
    return cal_l2_sum(y_real, 1)

def loss_func_adv_gen(y_fake):
    return cal_l2_sum(y_fake, 1)

def loss_func_tv(x_out):
    xp = cuda.get_array_module(x_out.data)
    b, ch, h, w = x_out.data.shape
    Wx = xp.zeros((ch, ch, 2, 2), dtype="f")
    Wy = xp.zeros((ch, ch, 2, 2), dtype="f")
    for i in range(ch):
        Wx[i,i,0,0] = -1
        Wx[i,i,0,1] = 1
        Wy[i,i,0,0] = -1
        Wy[i,i,1,0] = 1
    return F.sum(F.convolution_2d(x_out, W=Wx) ** 2) + F.sum(F.convolution_2d(x_out, W=Wy) ** 2)


class Updater(chainer.training.StandardUpdater):

    def __init__(self, *args, **kwargs):
        self.gen_g, self.gen_f, self.dis_x, self.dis_y = kwargs.pop('models')
        params = kwargs.pop('params')
        self._lambda1 = params['lambda1']
        #self._lambda2 = params['lambda2']
        #self._lambda3 = params['lambda3']
        self._image_size = params['image_size']
        #self._lambda4 = params['lambda4']
        self._iter = 0
        self._max_buffer_size = 50
        xp = self.gen_g.xp
        self._buffer_x = xp.zeros((self._max_buffer_size , 3, self._image_size, self._image_size)).astype("f")
        self._buffer_y = xp.zeros((self._max_buffer_size , 3, self._image_size, self._image_size)).astype("f")
    #    self._buffer_cnt = 0
        #self._buffer
        super(Updater, self).__init__(*args, **kwargs)

    def getAndUpdateBufferX(self, data, size):
        if self._iter < self._max_buffer_size:
            self._buffer_x[self._iter, :] = data[0]
        else:
            self._buffer_x[0:self._max_buffer_size-2, :] = self._buffer_x[1:self._max_buffer_size-1, :]
            self._buffer_x[self._max_buffer_size-1, : ]=data[0]

        if self._iter < size:
            return self._buffer_x[0:self._iter, :]
        if self._iter < self._max_buffer_size:
            return self._buffer_x[self._iter-size:self._iter, :]
        return self._buffer_x[self._max_buffer_size-1-size:self._max_buffer_size-1, :]

        #id = self._iter % self._max_buffer_size
        #self._buffer_x[id, :] = data[0]
        #if self._iter < self._max_buffer_size:
        #    return self._buffer_x[0:id]
        #return self._buffer_x

    def getAndUpdateBufferY(self, data, size):
        if self._iter < self._max_buffer_size:
            self._buffer_y[self._iter, :] = data[0]
        else:
            self._buffer_y[0:self._max_buffer_size-2, :] = self._buffer_y[1:self._max_buffer_size-1, :]
            self._buffer_y[self._max_buffer_size-1, : ]=data[0]

        if self._iter < size:
            return self._buffer_y[0:self._iter, :]
        if self._iter < self._max_buffer_size:
            return self._buffer_y[self._iter-size:self._iter, :]
        return self._buffer_y[self._max_buffer_size-1-size:self._max_buffer_size-1, :]


    def update_core(self):
        xp = self.gen_g.xp
    #    print(self._iter)
        self._iter += 1
        #print(self._iter)
        batch = self.get_iterator('main').next()
        batch_dis = self.get_iterator('dis').next()

        batchsize = len(batch)
        batchsize_dis = len(batch_dis)

        w_in = self._image_size

        x = xp.zeros((batchsize, 3, w_in, w_in)).astype("f")
        y = xp.zeros((batchsize, 3, w_in , w_in)).astype("f")

        for i in range(batchsize):
            #print(batch[i][0].shape)
            #print(batch[i][1].shape)
            x[i, :] = xp.asarray(batch[i][0])
            y[i, :] = xp.asarray(batch[i][1])

        x = Variable(x)
        y = Variable(y)

        x_dis = xp.zeros((batchsize_dis, 3, w_in, w_in)).astype("f")
        y_dis = xp.zeros((batchsize_dis, 3, w_in , w_in)).astype("f")
        for i in range(batchsize_dis):
            x_dis[i, :] = xp.asarray(batch_dis[i][0])
            y_dis[i, :] = xp.asarray(batch_dis[i][1])

        x_dis = Variable(x_dis)
        y_dis = Variable(y_dis)

        x_y = self.gen_g(x)
        x_y_copy = self.getAndUpdateBufferX(x_y.data, batchsize_dis)#Variable(x_y.data)
        x_y_copy = Variable(x_y_copy)
        x_y_x = self.gen_f(x_y)

        y_x = self.gen_f(y)
        y_x_copy = self.getAndUpdateBufferY(y_x.data, batchsize_dis) #Variable(y_x.data)
        #print(x_y_copy.shape)
        #print(x_dis.data.shape)
        y_x_copy = Variable(y_x_copy)
        y_x_y = self.gen_g(y_x)

        opt_g = self.get_optimizer('gen_g')
        opt_f = self.get_optimizer('gen_f')
        opt_x = self.get_optimizer('dis_x')
        opt_y = self.get_optimizer('dis_y')

        opt_g.zero_grads()
        opt_f.zero_grads()
        opt_x.zero_grads()
        opt_y.zero_grads()

        loss_dis_y_fake = loss_func_adv_dis_fake(self.dis_y(x_y_copy))
        loss_dis_y_real = loss_func_adv_dis_real(self.dis_y(y_dis))
        loss_dis_y = loss_dis_y_fake + loss_dis_y_real
        chainer.report({'loss': loss_dis_y}, self.dis_y)

        loss_dis_x_fake = loss_func_adv_dis_fake(self.dis_x(y_x_copy))
        loss_dis_x_real = loss_func_adv_dis_real(self.dis_x(x_dis))
        loss_dis_x = loss_dis_x_fake + loss_dis_x_real
        chainer.report({'loss': loss_dis_x}, self.dis_x)

        loss_dis_y.backward()
        loss_dis_x.backward()

        opt_y.update()
        opt_x.update()

        loss_gen_g_adv = loss_func_adv_gen(self.dis_y(x_y))
        loss_gen_f_adv = loss_func_adv_gen(self.dis_x(y_x))

        loss_cycle_x = self._lambda1 * loss_func_rec_l1(x_y_x, x)
        loss_cycle_y = self._lambda1 * loss_func_rec_l1(y_x_y, y)
        loss_gen = loss_gen_g_adv + loss_gen_f_adv + loss_cycle_x + loss_cycle_y
        loss_gen.backward()
        opt_f.update()
        opt_g.update()

        chainer.report({'loss_rec': loss_cycle_y}, self.gen_g)
        chainer.report({'loss_rec': loss_cycle_x}, self.gen_f)
        chainer.report({'loss_adv': loss_gen_g_adv}, self.gen_g)
        chainer.report({'loss_adv': loss_gen_f_adv}, self.gen_f)