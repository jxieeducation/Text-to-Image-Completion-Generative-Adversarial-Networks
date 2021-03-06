from __future__ import division
import os
import time
from glob import glob
import tensorflow as tf
import numpy as np
from collections import namedtuple

from module import *
from utils import *
import pickle
import tensorlayer as tl
import random

class cyclegan(object):
    def __init__(self, sess, args):
        self.sess = sess
        self.batch_size = args.batch_size
        self.image_size = args.fine_size
        self.input_c_dim = args.input_nc
        self.output_c_dim = args.output_nc
        self.L1_lambda = args.L1_lambda
        self.dataset_dir = args.dataset_dir

        self.discriminator = discriminator
        self.generator = generator_resnet

        if args.use_lsgan:
            self.criterionGAN = mae_criterion
        else:
            self.criterionGAN = sce_criterion

        OPTIONS = namedtuple('OPTIONS', 'batch_size image_size \
                              gf_dim df_dim output_c_dim is_training')
        self.options = OPTIONS._make((args.batch_size, args.fine_size,
                                      args.ngf, args.ndf, args.output_nc,
                                      args.phase == 'train'))

        self._build_model()
        self.saver = tf.train.Saver()
        self.pool = ImagePool(args.max_size)
        f = open("./datasets/_vocab_t2i.pickle", 'rb')
        self.vocab = pickle.load(f)
        f = open("./datasets/_caption_t2i.pickle", 'rb')
        self.caption_ids = pickle.load(f) # map of {id: [[caption ids]]}


    def _build_model(self):
        self.real_data = tf.placeholder(tf.float32,
                                        [None, self.image_size, self.image_size,
                                         self.input_c_dim * 4],
                                        name='real_A_and_B_images')
        self.real_A = self.real_data[:, :, :, :self.input_c_dim]
        self.real_B = self.real_data[:, :, :, self.input_c_dim:self.input_c_dim*2]
        self.wrong_A = self.real_data[:, :, :, self.input_c_dim*2:self.input_c_dim*3]
        self.wrong_B = self.real_data[:, :, :, self.input_c_dim*3:self.input_c_dim*4]


        self.captionA = tf.placeholder(dtype=tf.int64, shape=[None, None], name='captionA')
        self.captionB = tf.placeholder(dtype=tf.int64, shape=[None, None], name='captionB')
        self.captionWrong = tf.placeholder(dtype=tf.int64, shape=[None, None], name='captionWrong')

        self.captionANet = rnn_embed(self.captionA, is_train=False, reuse=False)
        self.captionBNet = rnn_embed(self.captionB, is_train=False, reuse=True)
        self.captionWrongNet = rnn_embed(self.captionWrong, is_train=False, reuse=True)

        load_and_assign_npz(sess=self.sess, name='datasets/net_rnn.npz', model=self.captionANet)
        load_and_assign_npz(sess=self.sess, name='datasets/net_rnn.npz', model=self.captionBNet)
        load_and_assign_npz(sess=self.sess, name='datasets/net_rnn.npz', model=self.captionWrongNet)

        self.fake_B = self.generator(self.real_A, self.captionANet.outputs, self.options, False, name="generatorA2B")
        self.fake_A_ = self.generator(self.fake_B, self.captionANet.outputs, self.options, False, name="generatorB2A")
        self.fake_A = self.generator(self.real_B, self.captionBNet.outputs, self.options, True, name="generatorB2A")
        self.fake_B_ = self.generator(self.fake_A, self.captionBNet.outputs, self.options, True, name="generatorA2B")

        self.DB_fake = self.discriminator(self.fake_B, self.captionANet.outputs, self.options, reuse=False, name="discriminatorB")
        self.DA_fake = self.discriminator(self.fake_A, self.captionBNet.outputs, self.options, reuse=False, name="discriminatorA")

        self.g_loss_a2b = self.criterionGAN(self.DB_fake, tf.ones_like(self.DB_fake))
        self.g_loss_b2a = self.criterionGAN(self.DA_fake, tf.ones_like(self.DA_fake))
        self.g_loss = self.criterionGAN(self.DA_fake, tf.ones_like(self.DA_fake)) \
            + self.criterionGAN(self.DB_fake, tf.ones_like(self.DB_fake)) \
            + self.L1_lambda * abs_criterion(self.real_A, self.fake_A_) \
            + self.L1_lambda * abs_criterion(self.real_B, self.fake_B_)

        self.fake_A_sample = tf.placeholder(tf.float32,
                                            [None, self.image_size, self.image_size,
                                             self.input_c_dim], name='fake_A_sample')
        self.fake_B_sample = tf.placeholder(tf.float32,
                                            [None, self.image_size, self.image_size,
                                             self.output_c_dim], name='fake_B_sample')

        self.DA_real = self.discriminator(self.real_A, self.captionANet.outputs, self.options, reuse=True, name="discriminatorA")
        self.DB_real = self.discriminator(self.real_B, self.captionBNet.outputs, self.options, reuse=True, name="discriminatorB")
        self.DB_fake_sample = self.discriminator(self.fake_B_sample, self.captionANet.outputs, 
            self.options, reuse=True, name="discriminatorB")
        self.DA_fake_sample = self.discriminator(self.fake_A_sample, self.captionBNet.outputs, 
            self.options, reuse=True, name="discriminatorA")

        self.DA_wrong_caption = self.discriminator(self.real_A, self.captionWrongNet.outputs, self.options, reuse=True, name="discriminatorA")
        self.DB_wrong_caption = self.discriminator(self.real_B, self.captionWrongNet.outputs, self.options, reuse=True, name="discriminatorB")
        self.DA_wrong_image = self.discriminator(self.wrong_A, self.captionANet.outputs, self.options, reuse=True, name="discriminatorA")
        self.DB_wrong_image = self.discriminator(self.wrong_B, self.captionBNet.outputs, self.options, reuse=True, name="discriminatorB")

        self.db_loss_real = self.criterionGAN(self.DB_real, tf.ones_like(self.DB_real))
        self.db_loss_fake = self.criterionGAN(self.DB_fake_sample, tf.zeros_like(self.DB_fake_sample))
        self.db_loss_wrong_caption = self.criterionGAN(self.DB_wrong_caption, tf.zeros_like(self.DB_wrong_caption))
        self.db_loss_wrong_image = self.criterionGAN(self.DB_wrong_image, tf.zeros_like(self.DB_wrong_image))
        self.db_loss = self.db_loss_real + (self.db_loss_fake + self.db_loss_wrong_caption + self.db_loss_wrong_image) / 3
        # self.db_loss = (self.db_loss_real + self.db_loss_fake) / 2

        self.da_loss_real = self.criterionGAN(self.DA_real, tf.ones_like(self.DA_real))
        self.da_loss_fake = self.criterionGAN(self.DA_fake_sample, tf.zeros_like(self.DA_fake_sample))
        self.da_loss_wrong_caption = self.criterionGAN(self.DA_wrong_caption, tf.zeros_like(self.DA_wrong_caption))
        self.da_loss_wrong_image = self.criterionGAN(self.DA_wrong_image, tf.zeros_like(self.DA_wrong_image))
        self.da_loss = self.da_loss_real + (self.da_loss_fake + self.da_loss_wrong_caption + self.da_loss_wrong_image) / 3
        self.da_lang_loss = self.da_loss_real + (self.da_loss_wrong_caption + self.da_loss_wrong_image) / 2
        # self.da_loss = self.da_loss_real + self.da_loss_fake

        self.d_loss = self.da_loss + self.db_loss

        self.g_loss_a2b_sum = tf.summary.scalar("g_loss_a2b", self.g_loss_a2b)
        self.g_loss_b2a_sum = tf.summary.scalar("g_loss_b2a", self.g_loss_b2a)
        self.g_loss_sum = tf.summary.scalar("g_loss", self.g_loss)
        self.g_sum = tf.summary.merge([self.g_loss_a2b_sum, self.g_loss_b2a_sum, self.g_loss_sum])

        self.db_loss_sum = tf.summary.scalar("db_loss", self.db_loss)
        self.da_loss_sum = tf.summary.scalar("da_loss", self.da_loss)
        self.d_loss_sum = tf.summary.scalar("d_loss", self.d_loss)
        self.db_loss_real_sum = tf.summary.scalar("db_loss_real", self.db_loss_real)
        self.db_loss_fake_sum = tf.summary.scalar("db_loss_fake", self.db_loss_fake)
        self.db_loss_wrong_caption_sum = tf.summary.scalar("db_loss_wrong_caption", self.db_loss_wrong_caption)
        self.db_loss_wrong_image_sum = tf.summary.scalar("db_loss_wrong_image", self.db_loss_wrong_image)
        self.da_loss_real_sum = tf.summary.scalar("da_loss_real", self.da_loss_real)
        self.da_loss_fake_sum = tf.summary.scalar("da_loss_fake", self.da_loss_fake)
        self.da_loss_wrong_caption_sum = tf.summary.scalar("da_loss_wrong_caption", self.da_loss_wrong_caption)
        self.da_loss_wrong_image_sum = tf.summary.scalar("da_loss_wrong_image", self.da_loss_wrong_image)
        self.d_sum = tf.summary.merge(
            [self.da_loss_sum, self.da_loss_real_sum, self.da_loss_fake_sum,
             self.db_loss_sum, self.db_loss_real_sum, self.db_loss_fake_sum,
             self.db_loss_wrong_caption_sum, self.db_loss_wrong_image_sum,
             self.da_loss_wrong_caption_sum, self.da_loss_wrong_image_sum,
             self.d_loss_sum]
        )

        self.test_A = tf.placeholder(tf.float32,
                                     [None, self.image_size, self.image_size,
                                      self.input_c_dim], name='test_A')
        self.test_B = tf.placeholder(tf.float32,
                                     [None, self.image_size, self.image_size,
                                      self.output_c_dim], name='test_B')

        self.testCaption = tf.placeholder(dtype=tf.int64, shape=[None, None], name='testCaption')
        self.testCaptionNet = rnn_embed(self.testCaption, is_train=False, reuse=True)
        load_and_assign_npz(sess=self.sess, name='datasets/net_rnn.npz', model=self.testCaptionNet)
        self.testB = self.generator(self.test_A, self.testCaptionNet.outputs, self.options, True, name="generatorA2B")
        self.testA = self.generator(self.test_B, self.testCaptionNet.outputs, self.options, True, name="generatorB2A")

        t_vars = tf.trainable_variables()
        self.d_vars = [var for var in t_vars if 'discriminator' in var.name]
        self.g_vars = [var for var in t_vars if 'generator' in var.name]
        for var in t_vars: print(var.name)

    def train(self, args):
        """Train cyclegan"""
        self.lr = tf.placeholder(tf.float32, None, name='learning_rate')
        self.d_optim = tf.train.AdamOptimizer(self.lr, beta1=args.beta1) \
            .minimize(self.d_loss, var_list=self.d_vars)
        self.d_lang_optim = tf.train.AdamOptimizer(self.lr, beta1=args.beta1) \
            .minimize(self.da_lang_loss, var_list=self.d_vars)
        self.g_optim = tf.train.AdamOptimizer(self.lr, beta1=args.beta1) \
            .minimize(self.g_loss, var_list=self.g_vars)

        init_op = tf.global_variables_initializer()
        self.sess.run(init_op)
        self.writer = tf.summary.FileWriter("./logs", self.sess.graph)

        counter = 1
        start_time = time.time()

        if args.continue_train:
            if self.load(args.checkpoint_dir):
                print(" [*] Load SUCCESS")
            else:
                print(" [!] Load failed...")

        for epoch in range(args.epoch):
            dataA = glob('./datasets/{}/*.*'.format(self.dataset_dir + '/trainA'))
            dataB = glob('./datasets/{}/*.*'.format(self.dataset_dir + '/trainB'))
            np.random.shuffle(dataA)
            np.random.shuffle(dataB)
            batch_idxs = min(min(len(dataA), len(dataB)), args.train_size) // self.batch_size
            lr = args.lr if epoch < args.epoch_step else args.lr*(args.epoch-epoch)/(args.epoch-args.epoch_step)

            for idx in range(0, batch_idxs):
                batch_files = list(zip(dataA[idx * self.batch_size:(idx + 1) * self.batch_size],
                                       dataB[idx * self.batch_size:(idx + 1) * self.batch_size],
                                       [dataA[random.choice(range(0, batch_idxs))]],
                                       [dataB[random.choice(range(0, batch_idxs))]]
                                       ))
                batch_images = [load_train_data(batch_file, args.load_size, args.fine_size) for batch_file in batch_files]
                batch_images = np.array(batch_images).astype(np.float32)
                
                nameA = dataA[idx * self.batch_size:(idx + 1) * self.batch_size][0]
                nameA = int(nameA.split("/")[-1].split(".")[0].split("_")[-1])
                nameB = dataB[idx * self.batch_size:(idx + 1) * self.batch_size][0]
                nameB = int(nameB.split("/")[-1].split(".")[0].split("_")[-1])
                
                captionA = [random.choice(self.caption_ids[nameA])]
                captionB = [random.choice(self.caption_ids[nameB])]
                captionWrong = [random.choice(self.caption_ids[random.choice(self.caption_ids.keys())])]

                captionA = tl.prepro.pad_sequences(captionA, padding='post')
                captionB = tl.prepro.pad_sequences(captionB, padding='post')
                captionWrong = tl.prepro.pad_sequences(captionWrong, padding='post')

                if epoch < 50:
                    # Update G network and record fake outputs
                    fake_A, fake_B, summary_str = self.sess.run(
                        [self.fake_A, self.fake_B, self.g_sum],
                        feed_dict={self.real_data: batch_images, self.lr: lr,
                        self.captionA: captionA, self.captionB: captionB, self.captionWrong: captionWrong})
                    self.writer.add_summary(summary_str, counter)
                    [fake_A, fake_B] = self.pool([fake_A, fake_B])

                    # Update D network
                    _, summary_str = self.sess.run(
                        [self.d_lang_optim, self.d_sum],
                        feed_dict={self.real_data: batch_images,
                                   self.fake_A_sample: fake_A,
                                   self.fake_B_sample: fake_B,
                                   self.lr: lr, 
                        self.captionA: captionA, self.captionB: captionB, self.captionWrong: captionWrong})
                else:
                    # Update G network and record fake outputs
                    fake_A, fake_B, _, summary_str = self.sess.run(
                        [self.fake_A, self.fake_B, self.g_optim, self.g_sum],
                        feed_dict={self.real_data: batch_images, self.lr: lr,
                        self.captionA: captionA, self.captionB: captionB, self.captionWrong: captionWrong})
                    self.writer.add_summary(summary_str, counter)
                    [fake_A, fake_B] = self.pool([fake_A, fake_B])

                    # Update D network
                    _, summary_str = self.sess.run(
                        [self.d_optim, self.d_sum],
                        feed_dict={self.real_data: batch_images,
                                   self.fake_A_sample: fake_A,
                                   self.fake_B_sample: fake_B,
                                   self.lr: lr, 
                        self.captionA: captionA, self.captionB: captionB, self.captionWrong: captionWrong})
                self.writer.add_summary(summary_str, counter)  

                counter += 1
                print(("Epoch: [%2d] [%4d/%4d] time: %4.4f" % (
                    epoch, idx, batch_idxs, time.time() - start_time)))

                if np.mod(counter, args.print_freq) == 1:
                    self.sample_model(args.sample_dir, epoch, idx)

                if np.mod(counter, args.save_freq) == 2:
                    self.save(args.checkpoint_dir, counter)

    def save(self, checkpoint_dir, step):
        model_name = "cyclegan.model"
        model_dir = "%s_%s" % (self.dataset_dir, self.image_size)
        checkpoint_dir = os.path.join(checkpoint_dir, model_dir)

        if not os.path.exists(checkpoint_dir):
            os.makedirs(checkpoint_dir)

        self.saver.save(self.sess,
                        os.path.join(checkpoint_dir, model_name),
                        global_step=step)

    def load(self, checkpoint_dir):
        print(" [*] Reading checkpoint...")

        model_dir = "%s_%s" % (self.dataset_dir, self.image_size)
        checkpoint_dir = os.path.join(checkpoint_dir, model_dir)

        ckpt = tf.train.get_checkpoint_state(checkpoint_dir)
        if ckpt and ckpt.model_checkpoint_path:
            ckpt_name = os.path.basename(ckpt.model_checkpoint_path)
            self.saver.restore(self.sess, os.path.join(checkpoint_dir, ckpt_name))
            return True
        else:
            return False

    def sample_model(self, sample_dir, epoch, idx):
        dataA = glob('./datasets/{}/*.*'.format(self.dataset_dir + '/testA'))
        dataB = glob('./datasets/{}/*.*'.format(self.dataset_dir + '/testB'))
        np.random.shuffle(dataA)
        np.random.shuffle(dataB)
        batch_files = list(zip(dataA[:self.batch_size], dataB[:self.batch_size], dataA[:self.batch_size], dataB[:self.batch_size]))
        sample_images = [load_train_data(batch_file, is_testing=True) for batch_file in batch_files]
        sample_images = np.array(sample_images).astype(np.float32)

        nameA = dataA[:self.batch_size][0]
        nameA = int(nameA.split("/")[-1].split(".")[0].split("_")[-1])
        nameB = dataB[:self.batch_size][0]
        nameB = int(nameB.split("/")[-1].split(".")[0].split("_")[-1])        
        captionA = [random.choice(self.caption_ids[nameA])]
        captionB = [random.choice(self.caption_ids[nameB])]
        captionA = tl.prepro.pad_sequences(captionA, padding='post')
        captionB = tl.prepro.pad_sequences(captionB, padding='post')

        fake_A, fake_B = self.sess.run(
            [self.fake_A, self.fake_B],
            feed_dict={self.real_data: sample_images, self.captionA: captionA, self.captionB: captionB}
        )
        real_A = sample_images[:, :, :, :self.input_c_dim]
        real_B = sample_images[:, :, :, self.input_c_dim:self.input_c_dim + self.output_c_dim]

        save_images(fake_A, [self.batch_size, 1],
                    './{}/A_{:02d}_{:04d}.jpg'.format(sample_dir, epoch, idx))
        save_images(real_B, [self.batch_size, 1],
                    './{}/A_{:02d}_{:04d}_real.jpg'.format(sample_dir, epoch, idx))

        save_images(fake_B, [self.batch_size, 1],
                    './{}/B_{:02d}_{:04d}.jpg'.format(sample_dir, epoch, idx))
        save_images(real_A, [self.batch_size, 1],
                    './{}/B_{:02d}_{:04d}_real.jpg'.format(sample_dir, epoch, idx))

    def test(self, args):
        """Test cyclegan"""
        init_op = tf.global_variables_initializer()
        self.sess.run(init_op)
        if args.which_direction == 'AtoB':
            sample_files = glob('./datasets/{}/*.*'.format(self.dataset_dir + '/testA'))
        elif args.which_direction == 'BtoA':
            sample_files = glob('./datasets/{}/*.*'.format(self.dataset_dir + '/testB'))
        else:
            raise Exception('--which_direction must be AtoB or BtoA')

        if self.load(args.checkpoint_dir):
            print(" [*] Load SUCCESS")
        else:
            print(" [!] Load failed...")

        # write html for visual comparison
        index_path = os.path.join(args.test_dir, '{0}_index.html'.format(args.which_direction))
        index = open(index_path, "w")
        index.write("<html><body><table><tr>")
        index.write("<th>name</th><th>input</th><th>output</th></tr>")

        out_var, in_var = (self.testB, self.test_A) if args.which_direction == 'AtoB' else (
            self.testA, self.test_B)

        for sample_file in sample_files:
            print('Processing image: ' + sample_file)

            nameA = int(sample_file.split("/")[-1].split(".")[0].split("_")[-1])
            captionA = [random.choice(self.caption_ids[nameA])]
            caption_str = " ".join([self.vocab.id_to_word(id) for id in captionA[0]])
            captionA = tl.prepro.pad_sequences(captionA, padding='post')

            sample_image = [load_test_data(sample_file, args.fine_size)]
            sample_image = np.array(sample_image).astype(np.float32)
            image_path = os.path.join(args.test_dir,
                                      '{0}_{1}'.format(args.which_direction, os.path.basename(sample_file)))
            fake_img = self.sess.run(out_var, feed_dict={in_var: sample_image, self.testCaption: captionA})
            save_images(fake_img, [1, 1], image_path)
            index.write("<td>%s</td>" % os.path.basename(image_path))
            index.write("<td>%s</td>" % caption_str)
            index.write("<td><img src='%s'></td>" % (sample_file if os.path.isabs(sample_file) else (
                '..' + os.path.sep + sample_file)))
            index.write("<td><img src='%s'></td>" % (image_path if os.path.isabs(image_path) else (
                '..' + os.path.sep + image_path)))
            index.write("</tr>")
        index.close()
