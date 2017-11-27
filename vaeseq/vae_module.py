"""Base classes for modules that implement sequential VAEs."""

import abc
import tensorflow as tf
import sonnet as snt

from . import context as context_mod
from . import dist_module
from . import util


class VAECore(dist_module.DistCore):
    """Base class for sequential VAE implementations."""

    def __init__(self, hparams, obs_encoder, obs_decoder, name=None):
        super(VAECore, self).__init__(name=name)
        self._hparams = hparams
        self._obs_encoder = obs_encoder
        self._obs_decoder = obs_decoder

    @abc.abstractmethod
    def infer_latents(self, contexts, observed):
        """Returns a sequence of latent states and their divergences."""

    @property
    def event_size(self):
        return self._obs_decoder.event_size

    @property
    def event_dtype(self):
        return self._obs_decoder.event_dtype

    def dist(self, params, name=None):
        return self._obs_decoder.dist(params, name=name)

    def evaluate(self, contexts, observed,
                 latents=None, initial_state=None, samples=1):
        """Evaluates the log-probabilities of each given observation."""
        cell, inputs = self.log_probs, (contexts, observed)
        if latents is not None:
            if initial_state is not None:
                raise ValueError("Cannot specify initial state and latents.")
            inputs = (inputs, latents)
            cell = util.use_recorded_state_rnn(cell)
        cell, inputs = util.add_support_for_scalar_rnn_inputs(cell, inputs)
        def _make_initial_state():
            if initial_state is not None:
                return initial_state
            batch_size = util.batch_size_from_nested_tensors(observed)
            return self.initial_state(batch_size)
        return _average_runs(samples, cell, inputs, _make_initial_state)

    def generate(self,
                 inputs,  # None | Tensors | Context.
                 context, # Context
                 batch_size=None,  # defaults to hparams.batch_size
                 sequence_size=None,  # defaults to hparams.sequence_size
                 initial_state=None,
                 inputs_initial_state=None,
                 context_initial_state=None):
        """Generates a sequence of observations."""
        if sequence_size is None:
            sequence_size = util.sequence_size(self._hparams)

        # Allow passing constant Tensors as inputs.
        if inputs is not None and not isinstance(inputs, context_mod.Context):
            inputs = context_mod.Constant(inputs, name="inputs")

        # Create initial states.
        infer_batch_size = batch_size
        if batch_size is None:
            infer_batch_size = util.batch_size(self._hparams)
        if inputs is not None and inputs_initial_state is None:
            inputs_initial_state = inputs.initial_state(infer_batch_size)
            if batch_size is None:
                infer_batch_size = util.batch_size_from_nested_tensors(
                    inputs_initial_state)
        if context_initial_state is None:
            context_initial_state = context.initial_state(infer_batch_size)
            if batch_size is None:
                infer_batch_size = util.batch_size_from_nested_tensors(
                    context_initial_state)
        if initial_state is None:
            initial_state = self.initial_state(infer_batch_size)

        # Chain inputs with context.
        if inputs is not None:
            context = context_mod.Chain([inputs, context])
            context_initial_state = (inputs_initial_state,
                                     context_initial_state)

        cell = util.state_recording_rnn(self.samples)
        cell_output_observations = lambda out: out[0]
        return context.drive_rnn(
            cell,
            sequence_size=sequence_size,
            initial_state=context_initial_state,
            cell_initial_state=initial_state,
            cell_output_dtype=(self.event_dtype, self.state_dtype),
            cell_output_observations=cell_output_observations)


def _average_runs(num_runs, cell, inputs, make_initial_state):
    """Run the RNN outputs over num_run runs."""
    def _run(unused_arg):
        del unused_arg
        return tf.nn.dynamic_rnn(
            cell, inputs,
            initial_state=make_initial_state(),
            dtype=tf.float32)[0]
    if num_runs == 1:
        return _run(None)
    runs = tf.map_fn(_run, tf.zeros([num_runs, 0]))
    return tf.reduce_mean(runs, axis=0)