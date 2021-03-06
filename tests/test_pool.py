# ----------------------------------------------------------------------------
# Copyright 2016 Nervana Systems Inc.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ----------------------------------------------------------------------------

import numpy as np

import ngraph as ng
import ngraph.transformers as ngt
from ngraph.util.utils import executor
from ngraph.util.utils import RandomTensorGenerator
from ngraph.op_graph.axes import spatial_axis
from ngraph.frontends.neon import ax, ar
from neon import NervanaObject
from neon.backends import gen_backend
from neon.layers.layer import Pooling

rng = RandomTensorGenerator(0, np.float32)

NervanaObject.be = gen_backend()


class DummyDeltaBuffers(object):
    """
    Dummy class for delta buffers needed by neon
    """
    def __init__(self):
        self.buffers = [None]


def test_pooling():
    """
    test pooling forward and backward path
    """
    N = 128
    C = 3
    D = 1
    H = W = 32

    J = T = 1
    R = S = 2
    ngt.make_transformer()

    padding = dict(pad_d=0, pad_h=0, pad_w=0, pad_c=0)
    strides = dict(str_d=1, str_h=1, str_w=1, str_c=1)
    fshape = dict(J=J, T=T, R=R, S=S)

    pool_params = dict(op='max')
    pool_params.update(padding)
    pool_params.update(strides)
    pool_params.update(fshape)

    ax_i = ng.make_axes([ax.C, ax.D, ax.H, ax.W, ax.N])
    ax_i.set_shape((C, D, H, W, N))
    inputs = ng.placeholder(axes=ax_i)

    ax_o = ng.make_axes([
        spatial_axis(ax_i, J, padding['pad_c'], strides['str_c'], role=ar.Channel),
        spatial_axis(ax_i, T, padding['pad_d'], strides['str_d'], role=ar.Depth),
        spatial_axis(ax_i, R, padding['pad_h'], strides['str_h'], role=ar.Height),
        spatial_axis(ax_i, S, padding['pad_w'], strides['str_w'], role=ar.Width),
        ax.N
    ])

    # randomly initialize
    input_value = rng.uniform(-1, 1, ax_i)

    assert input_value.shape == ax_i.lengths

    # compute convolution with graph
    output = ng.pooling(pool_params, inputs, axes=ax_o)
    targets = ng.placeholder(axes=ax_o)

    costs = ng.cross_entropy_binary(ng.sigmoid(output), targets)
    error = ng.sum(costs, out_axes=()) / ng.batch_size(costs)
    d_inputs = ng.deriv(error, inputs)

    targets_value = rng.uniform(.1, 0.9, output.axes)

    conv_executor = executor([output, error, d_inputs], inputs, targets)
    result_ng, err_ng, gradI_ng = conv_executor(input_value, targets_value)

    # Now compute reference values via NEON
    NervanaObject.be.bsz = N
    neon_layer = Pooling(fshape=fshape, padding=padding, strides=strides, op="max")

    inp = neon_layer.be.array(input_value.reshape(C * H * W * D, N))
    neon_layer.configure((C, H, W))
    neon_layer.prev_layer = True
    neon_layer.allocate()
    neon_layer.set_deltas(DummyDeltaBuffers())

    result_ne = neon_layer.fprop(inp).get().reshape(output.axes.lengths)

    act_result_ne = 1. / (1.0 + np.exp(-result_ne))
    err = neon_layer.be.array((act_result_ne - targets_value).reshape(-1, N) / float(N))
    gradI_ne = neon_layer.bprop(err).get().reshape(ax_i.lengths)

    # Compare fprop
    np.testing.assert_allclose(result_ng, result_ne, rtol=0, atol=1e-6)

    # Compare bprop
    np.testing.assert_allclose(gradI_ng, gradI_ne, rtol=0, atol=1e-6)
