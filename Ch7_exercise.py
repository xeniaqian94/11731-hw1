from collections import defaultdict
import dynet as dy
import numpy as np
import random
import sys
from util import *
from nltk.translate.bleu_score import corpus_bleu
import argparse


# 18.6 BLEU score

def get_batches(sents_pair, batch_size):
    length_bucket = defaultdict(list)
    [length_bucket[len(pair[0])].append(pair) for pair in sents_pair]
    batches = []
    for length in length_bucket:
        pairs = length_bucket[length]
        print length, len(pairs)
        batch_ids = [x * batch_size for x in range(len(pairs) / batch_size + 1)]
        random.shuffle(batch_ids)
        for i, sid in enumerate(batch_ids, 1):
            batches.append(list(zip(*pairs[sid:sid + batch_size])))
    random.shuffle(batches)
    for batch in batches:
        yield [list(batch[0]), list(batch[1])]


def test(args):
    training_src = read_corpus(args.train_src)  # get vocabulary
    src_vocab = Vocab.from_corpus(training_src, args.src_vocab_size)
    training_src_id = src_vocab.get_data_id(training_src)
    args['src_vocab'] = src_vocab
    args['training_src_id'] = training_src_id

    training_tgt = read_corpus(args.train_tgt)
    tgt_vocab = Vocab.from_corpus(training_tgt, args.tgt_vocab_size)
    training_tgt_id = tgt_vocab.get_data_id(training_tgt)
    args['tgt_vocab'] = tgt_vocab
    args['training_tgt_id'] = training_tgt_id

    test_src = read_corpus(args.test_src)
    test_src_id = src_vocab.get_data_id(test_src)
    test_tgt = read_corpus(args.test_tgt)
    test_tgt_id = src_vocab.get_data_id(test_tgt)

    model = EncoderDecoder(args, src_vocab, tgt_vocab, src_vocab.w2i, tgt_vocab.w2i)
    model.load()

    test_pair = zip(test_src_id, test_tgt_id)

    hypotheses, bleu_score = model.decode(test_pair, True)

    print "BLEU score on test set %f" % bleu_score


def blind_test(args):
    blind_src = read_corpus(args.blind_src)  # get vocabulary
    src_vocab = Vocab.from_corpus(blind_src, args.src_vocab_size)
    blind_src_id = get_data_id(src_vocab, blind_src)
    args['src_vocab'] = src_vocab
    args['blind_src_id'] = blind_src_id

    model = EncoderDecoder(args, src_vocab, None, src_vocab.w2i, NOne)
    model.load()

    # test_pair=zip(blind_src,np.zeros())

    # hypotheses, bleu_score = model.decode(test_pair, True)


def train(args):
    training_src = read_corpus(args.train_src)  # get vocabulary
    # print "training_src len " + str(len(training_src))
    src_vocab = Vocab.from_corpus(training_src, args.src_vocab_size)
    training_src_id = get_data_id(src_vocab, training_src)

    args.src_vocab = src_vocab
    args.training_src_id = training_src_id

    print "Length of train(src) " + str(len(training_src_id)) + " num of batches " + str(
        len(training_src_id) / args.batch_size + 1)

    training_tgt = read_corpus(args.train_tgt)
    tgt_vocab = Vocab.from_corpus(training_tgt, args.tgt_vocab_size)
    training_tgt_id = get_data_id(tgt_vocab, training_tgt)
    args.tgt_vocab = tgt_vocab
    args.training_tgt_id = training_tgt_id

    print "Length of train(tgt) " + str(len(training_tgt_id)) + " num of batches " + str(
        len(training_tgt_id) / args.batch_size + 1)

    dev_src = read_corpus(args.dev_src)  # get vocabulary
    dev_src_id = get_data_id(src_vocab, dev_src)

    dev_tgt = read_corpus(args.dev_tgt)  # get vocabulary
    dev_tgt_id = get_data_id(tgt_vocab, dev_tgt)

    print "Data duly loaded!"

    model = EncoderDecoder(args, src_vocab, tgt_vocab, src_vocab.w2i, tgt_vocab.w2i)

    train_pair = zip(training_src_id, training_tgt_id)
    dev_pair = zip(dev_src_id, dev_tgt_id)

    epochs = 20
    updates = 0
    eval_every = 3000
    prev_bleu = []
    bad_counter = 0
    total_loss = total_examples = 0
    start_time = time.time()
    for epoch in range(epochs):
        for (src_batch, tgt_batch) in get_batches(train_pair, args.batch_size):
            updates += 1
            batch_size = len(src_batch)

            if updates % eval_every == 0:
                bleu_score, translation = decode(model, dev_pair)
                print "Updates=%d, BlEU score = %f" % (updates, bleu_score)

                if len(prev_bleu) == 0 or bleu_score > max(prev_bleu):
                    bad_counter = 0
                    print "Saving the model %s" % (args.model_name)
                    model.save()
                else:
                    bad_counter += 1
                    if bad_counter >= args.tolerance:
                        print "Early stop!"
                        exit
                prev_bleu.append(bleu_score)
            src_encodings = model.encode(src_batch)
            decode_loss = model.decode_loss(src_encodings, tgt_batch)

            loss_value = decode_loss.value()
            total_loss += loss_value
            total_examples += batch_size

            ppl = np.exp(loss_value / sum([len(s) for s in tgt_batch]))
            print "Epoch=%d, Updates=%d, Loss=%f, Avg. Loss=%f, PPL=%f, Time taken=%d s" % \
                  (epoch + 1, updates + 1, loss_value, total_loss / total_examples, ppl,
                   time.time() - start_time)
            decode_loss.backward()
            model.trainer.update()
        model.trainer.update_epoch(1.0)


class EncoderDecoder:
    # define dynet model for the encoder-decoder model
    def __init__(self, args, src_vocab, tgt_vocab, src_id_to_token, tgt_id_to_token):
        self.model = dy.Model()
        self.trainer = dy.AdamTrainer(self.model)
        self.args = args
        # self.src_token_to_id = args['src_token_to_id']
        self.src_vocab, self.src_token_to_id, self.src_id_to_token = src_vocab, src_vocab.w2i, src_vocab.i2w
        self.src_vocab_size = self.src_vocab.size()

        # self.tgt_token_to_id = args['tgt_token_to_id']
        self.tgt_vocab, self.tgt_id_to_token, self.tgt_token_to_id = tgt_vocab, tgt_vocab.w2i, tgt_vocab.i2w
        self.tgt_vocab_size = self.tgt_vocab.size()

        self.embed_size = args.embed_size
        self.hidden_size = args.hidden_size
        self.layers = args.layers

        self.src_lookup = self.model.add_lookup_parameters((self.src_vocab_size, self.embed_size))
        self.tgt_lookup = self.model.add_lookup_parameters((self.tgt_vocab_size, self.embed_size))

        self.l2r_builder = dy.GRUBuilder(self.layers, self.embed_size, self.hidden_size, self.model)
        self.r2l_builder = dy.GRUBuilder(self.layers, self.embed_size, self.hidden_size, self.model)
        self.dec_builder = dy.GRUBuilder(self.layers, self.embed_size + 2 * self.hidden_size, self.hidden_size,
                                         self.model)

        self.W_h = self.model.add_parameters((self.embed_size, self.hidden_size * 3))
        self.b_h = self.model.add_parameters((self.embed_size))
        self.b_h.zero()

        # initial input parameter for stage 0 in decoding
        self.W_init = self.model.add_parameters((self.hidden_size, args.hidden_size * 2))
        self.b_init = self.model.add_parameters((self.hidden_size))
        self.b_init.zero()

        # target word softmax
        self.W_y = self.model.add_parameters((self.tgt_vocab_size, self.embed_size))
        self.b_y = self.model.add_parameters((self.tgt_vocab_size))
        self.b_y.zero()

        # attention
        self.W1_att_f = self.model.add_parameters((self.attention_size, self.hidden_size * 2))
        self.W1_att_e = self.model.add_parameters((self.attention_size, self.hidden_size))
        self.w2_att = self.model.add_parameters((self.attention_size))

    # Training step over a single sentence pair
    def save(self):
        self.model.save("../model/" + self.args['model_name'])

    def load(self):
        self.model.load("../model/" + self.args['model_name'])

    def __step(self, instance):
        dy.renew_cg()
        W_y = dy.parameter(self.W_y)
        b_y = dy.parameter(self.b_y)

        src_sent, tgt_sent = instance
        losses = []
        total_words = 0

        # Start the rnn for the encoder
        enc_state = self.enc_builder.initial_state()
        for cw in src_sent:
            x_t = dy.lookup(self.src_lookup, int(cw))
            enc_state = enc_state.add_input(x_t)
        encoded = enc_state.output()

        # Set initial decoder state to the result of the encoder
        dec_state = self.dec_builder.initial_state([encoded])
        errs = []

        # Calculate losses for decoding
        for (cw, nw) in zip(tgt_sent, tgt_sent[1:]):
            x_t = dy.lookup(self.tgt_lookup, int(cw))
            dec_state = dec_state.add_input(x_t)
            y_t = dec_state.output()
            r_t = dy.affine_transform([b_y, W_y, y_t])
            err = dy.pickneglogsoftmax(r_t, int(nw))
            errs.append(err)
            total_words += 1

        return dy.esum(losses), total_words
        return

    def encode(self, src_sents):

        dy.renew_cg()
        l2r_state = self.l2r_builder.initial_state()
        r2l_state = self.r2l_builder.initial_state()

        wids, masks = transpose_batch(src_sents)

        l2r_wid_embeds = [dy.lookup_batch(self.src_lookup, wid) for wid in wids]
        r2l_wid_embeds = l2r_wid_embeds[::-1]
        l2r_encodings = l2r_state.transduce(l2r_wid_embeds)
        r2l_encodings = r2l_state.transduce(r2l_wid_embeds)

        return dy.concatenate(
            [l2r_encoding, r2l_encoding] for (l2r_encoding, r2l_encoding) in zip(l2r_encodings, r2l_encodings))

    def decode_loss(self, encoding, tgt_sents):
        batch_size = len(tgt_sents)
        maxLen = max(len(tgt_sent) for tgt_sent in tgt_sents)

        W_init = dy.parameter(self.W_init)
        b_init = dy.parameter(self.b_init)
        W_h = dy.parameter(self.W_h)
        b_h = dy.parameter(self.b_h)
        W_y = dy.parameter(self.W_y)
        b_y = dy.parameter(self.b_y)

        dec_state = self.dec_builder.initial_state([dy.tanh(dy.affine_transform([b_init, W_init, encoding[-1]]))])
        tgt_wids, tgt_masks = self.model.transpose_batch(tgt_sents)
        ctx = dy.vecInput(self.args.hidden_size * 2)
        losses = []
        for i in range(1, maxLen):
            tgt_emb = dy.lookup_batch(self.args.tgt_lookup, tgt_wids[i - 1])
            x = dy.concatenate([tgt_emb, ctx])  # equation 74
            dec_state = dec_state.add_input(x)
            hid = dec_state.output()  # equaltion 74
            ctx, alpha_t = self.attention(encoding, hid, batch_size)
            readout = dy.tanh(dy.affine_transform([b_h, W_h, dy.concatenate([hid, ctx])]))
            y_t = dy.affine_transform([b_y, W_y, readout])

            loss = dy.pickneglogsoftmax_batch(y_t, tgt_wids[i])
            if tgt_masks[i][-1] != 1:
                mask_expr = dy.inputVector(tgt_masks[i])
            # # print len(mask)
            mask_expr = dy.reshape(mask_expr, (1,), len(tgt_masks[i]))
            loss = loss * mask_expr
            losses.append(loss)

        return dy.esum(losses)

    def attention(self, encoding, hidden, batch_size):  # calculating attention score
        # attention
        W1_att_f = dy.parameter(self.W1_att_f)
        W1_att_e = dy.parameter(self.W1_att_e)
        w2_att = dy.parameter(self.w2_att)

        H = dy.concatenate_cols(encoding)

        a = dy.softmax(dy.reshape(
            w2_att * dy.tanh(dy.colwise_add(W1_att_f * H, W1_att_e * hidden)), (len(encoding),),
            batch_size))  # equation 81

        return H * a, a

    def translate(self, src_sent, max_len=200):

        beam_size = self.args.beam_size
        print "Beam size %d " % beam_size

        encodings = self.encode(src_sent)

        W_h = dy.parameter(self.W_h)
        b_h = dy.parameter(self.b_h)

        # initial input parameter for stage 0 in decoding
        W_init = dy.parameter(self.W_init)
        b_init = _dy.parameter(self.b_init)

        # target word softmax
        W_y = dy.parameter(self.W_y)
        b_y = dy.parameter(self.b_y)

        completed_hypotheses = []
        hypotheses = [Hypothesis(
            state=self.dec_builder.initial_state([dy.tanh(W_init * encodings[-1] + b_init)]),
            y=[self.tgt_vocab['<s>']],
            ctx_tm1=dy.vecInput(self.args.hidden_size * 2),
            score=0.)]

        t = 0
        while len(completed_hypotheses) < beam_size and t < max_len:
            t += 1
            new_hyp_scores_list = []
            for hyp in hypotheses:
                y_tm1_embed = dy.lookup(self.tgt_lookup, hyp.y[-1])
                hyp.state = hyp.state.add_input(dy.concatenate([y_tm1_embed, hyp.ctx_tm1]))
                h_t = hyp.state.output()
                ctx, alpha_t = self.attention(encodings, h_t, 1)
                read_out = dy.tanh(dy.affine_transform([b_h, W_h, dy.concatenate([h_t, ctx])]))
                y_t = W_y * read_out + b_y
                p_t = dy.log_softmax(y_t).npvalue()
                hyp.ctx_tm1 = ctx
                new_hyp_scores_list.append(hyp.score + p_t)

            new_hyp_scores = np.concatenate(new_hyp_scores_list).flatten()
            new_hyp_pos = (-new_hyp_scores).argsort()[:(beam_size - len(completed_hypotheses))]

            prev_hyp_ids = new_hyp_pos / self.args.tgt_vocab_size
            word_ids = new_hyp_pos % self.args.tgt_vocab_size
            new_hyp_scores = new_hyp_scores[new_hyp_pos]

            new_hypotheses = []

            for prev_hyp_id, word_id, hyp_score in zip(prev_hyp_ids, word_ids, new_hyp_scores):
                prev_hyp = hypotheses[prev_hyp_id]
                hyp = Hypothesis(state=prev_hyp.state,
                                 y=prev_hyp.y + [word_id],
                                 ctx_tm1=prev_hyp.ctx_tm1,
                                 score=hyp_score)

                if word_id == self.tgt_vocab['</s>']:
                    completed_hypotheses.append(hyp)
                else:
                    new_hypotheses.append(hyp)

            hypotheses = new_hypotheses

        if len(completed_hypotheses) == 0:
            completed_hypotheses = [hypotheses[0]]  # if there's no good finished  hypotheses.

        for hyp in completed_hypotheses:
            hyp.y = [self.tgt_vocab_id2word[i] for i in hyp.y]

        return sorted(completed_hypotheses, key=lambda x: x.score, reverse=True)


def decode(self, data_pairs, with_reference=False):
    hypotheses = []
    bleu_score = 0

    for src_sent, tgt_sent in data_pairs:
        hypothesis = self.translate(src_sent)[0]
        hypotheses.append(hypothesis)

    if with_reference:
        bleu_score = corpus_bleu([[tgt_sent[1:-1]] for src_sent, tgt_sent in data_pairs],
                                 [hypothesis[1:-1] for hypothesis in hypotheses])
    f = open(self.args.output + self.args.model_name, "w")
    for hypothesis in hypotheses:
        f.write(" ".join(hypothesis[1:-1]) + "\n")

    return hypotheses, bleu_score


def transpose_batch(self, src_batch):
    maxLen = max([len(sent) for sent in src_batch])
    wids = []
    masks = []
    for i in range(maxLen):
        wids.append([(sent[i] if len(sent) > i else 2) for sent in src_batch])  # w2i["</s>"]==2
        masks.append([(1 if len(sent) > i else 0) for sent in src_batch])
    return wids, masks


def __step_batch(self, batch):
    dy.renew_cg()
    W_y = dy.parameter(self.W_y)
    b_y = dy.parameter(self.b_y)

    src_batch = [x[0] for x in batch]
    tgt_batch = [x[1] for x in batch]

    # Encoder
    # src_batch  = [ [a1,a2,a3,a4,a5], [b1,b2,b3,b4,b5], [c1,c2,c3,c4], ...]
    # transpose the batch into
    #   src_cws: [[a1,b1,c1,..], [a2,b2,c2,..], ... [a5,b5,END,...]]
    #   src_len: [5,5,4,...]

    src_cws = transpose_batch(src_batch)

    src_len = [len(sent) for sent in src_batch]

    encodings = []
    enc_state = self.enc_builder.initial_state()
    for i, cws in enumerate(src_cws):
        enc_state = XXXX  # lookup_batch
        encodings.append(enc_state.output())

    # We want to extract the correct encodings for the correct timestep for each sentence,
    # then reconstruct the state so that they are the same dimensions as the decoder's state
    #   src_encodings: [e(a)5, e(b)5, e(c)4, ...]
    src_encodings = []
    for i, l in enumerate(src_len):
        # Note: This is a static implementation of what you need to do
        src_encodings.append(encodings[l - 1].npvalue()[:, 0, i])

    encoded = XXX(src_encodings)

    losses = []
    total_words = 0

    # Decoder
    # tgt_batch  = [ [a1,a2,a3,a4,a5], [b1,b2,b3,b4,b5], [c1,c2,c3,c4] ..]
    # transpose the batch into
    #   tgt_cws: [[a1,b1,c1,..], [a2,b2,c2,..], .. [a5,b5,END, ...]]
    #   masks: [1,1,1,..], [1,1,1,..], ...[1,1,0,..]]
    wids = []
    masks = []
    # print "len sents[0]"+str(len(sents[0])) +" number within this batch "+str(len(sents))
    for i in range(len(sents[0])):
        wids.append([(vw.word2Wid(sent[i]) if len(sent) > i else S) for sent in sents])
        mask = [(1 if len(sent) > i else 0) for sent in sents]
        # print "len of mask "+str(len(mask))
        masks.append(mask)
        tot_words += sum(mask)

    tgt_cws = []
    masks = []
    total_words = XXXX

    dec_state = self.dec_builder.initial_state([encoded])
    for i, (cws, nws, mask) in enumerate(zip(tgt_cws, tgt_cws[1:], masks)):
        dec_state = XXXX  # lookup_batch
        y_star = XXXX([b_y, W_y, dec_state.output()])
        loss = XXXX(y_star, nws)  # pickneglogsoftmax_batch
        mask_loss = XXX(mask, loss)
        losses.append(mask_loss)

    return dy.sum_batches(dy.esum(losses)), total_words


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--train_src', type=str, default="./en-de/train.en-de.low.filt.de")
    parser.add_argument('--train_tgt', type=str, default="./en-de/train.en-de.low.filt.en")
    parser.add_argument('--dev_src', type=str, default="./en-de/valid.en-de.low.de")
    parser.add_argument('--dev_tgt', type=str, default="./en-de/valid.en-de.low.en")
    parser.add_argument('--test_src', type=str, default="./en-de/test.en-de.low.de")
    parser.add_argument('--test_tgt', type=str, default="./en-de/test.en-de.low.en")
    parser.add_argument('--blind_src', type=str, default="./en-de/blind.en-de.low.de")
    parser.add_argument('--train', action="store_false", default=True)

    parser.add_argument("--layers", type=int, default=1)
    parser.add_argument("--embed_size", type=int, default=512)
    parser.add_argument("--hidden_size", type=int, default=512)
    parser.add_argument("--att_dim", type=int, default=256)
    parser.add_argument("--beam_size", type=int, default=5)
    parser.add_argument("--batch_size", type=int, default=16)

    parser.add_argument('--src_vocab_size', type=int, default=20000)
    parser.add_argument('--tgt_vocab_size', type=int, default=20000)

    parser.add_argument('--load_from')
    parser.add_argument('--concat_readout', action='store_true', default=False)
    parser.add_argument('--tolerance', type=int, default=10)
    parser.add_argument('--model_name', type=str)
    parser.add_argument('--output', type=str, default='../output/')
    parser.add_argument('--dropout', type=float, default=0.5)

    parser.add_argument('--dynet-mem', default="6000,5000,1000", type=str)
    parser.add_argument('--random_seed', default=235109662, type=int)
    parser.add_argument('--for_loop_att', action="store_true", default=False)
    args = parser.parse_args()
    np.random.seed(args.random_seed * 13 / 7)

    dyparams = dy.DynetParams()
    dyparams.from_args()
    print dyparams.keys()
    dyparams.set_mem(args.dynet_mem)
    dyparams.init()

    if args.train:
        train(args)
    else:
        test(args)

    encdec = EncoderDecoder(args)


if __name__ == '__main__': main()