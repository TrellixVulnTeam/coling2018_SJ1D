import logging
import time
from enum import Enum

from ignite._utils import _to_hours_mins_secs


class Events(Enum):
    EPOCH_STARTED = "epoch_started"
    EPOCH_COMPLETED = "epoch_completed"
    STARTED = "started"
    COMPLETED = "completed"
    ITERATION_STARTED = "iteration_started"
    ITERATION_COMPLETED = "iteration_completed"
    EXCEPTION_RAISED = "exception_raised"


class State(object):
    def __init__(self, **kwargs):
        self.iteration = 0
        self.output = None
        self.batch = None
        for k, v in kwargs.items():
            setattr(self, k, v)


class Engine(object):
    """Runs a given process_function over each batch of a dataset, emitting events as it goes.

    Args:
        process_function (Callable): A function receiving a handle to the engine and the current batch
            in each iteration, outputing data to be stored in the state

    """
    def __init__(self, process_function):
        self._event_handlers = {}
        self._logger = logging.getLogger(__name__ + "." + self.__class__.__name__)
        self._logger.addHandler(logging.NullHandler())
        self._process_function = process_function
        self.should_terminate = False
        self.state = None
        if self._process_function is None:
            raise ValueError("Engine must be given a processing function in order to run")

    def add_event_handler(self, event_name, handler, *args, **kwargs):
        """Add an event handler to be executed when the specified event is fired

        Args:
            event_name (Events): event from ignite.engines.Events to attach the handler to
            handler (Callable): the callable event handler that should be invoked
            *args: optional args to be passed to `handler`
            **kwargs: optional keyword args to be passed to `handler`

        """
        if event_name not in Events.__members__.values():
            self._logger.error("attempt to add event handler to an invalid event %s ", event_name)
            raise ValueError("Event {} is not a valid event for this Engine".format(event_name))

        if event_name not in self._event_handlers:
            self._event_handlers[event_name] = []

        self._event_handlers[event_name].append((handler, args, kwargs))
        self._logger.debug("added handler for event %s ", event_name)

    def on(self, event_name, *args, **kwargs):
        """Decorator shortcut for add_event_handler

        Args:
            event_name (Events): event to attach the handler to
            *args: optional args to be passed to `handler`
            **kwargs: optional keyword args to be passed to `handler`

        """
        def decorator(f):
            self.add_event_handler(event_name, f, *args, **kwargs)
            return f
        return decorator

    def _fire_event(self, event_name, *event_args):
        if event_name in self._event_handlers.keys():
            self._logger.debug("firing handlers for event %s ", event_name)
            for func, args, kwargs in self._event_handlers[event_name]:
                func(self, *(event_args + args), **kwargs)

    def terminate(self):
        """Sends terminate signal to the engine, so that it terminates after the current iteration
        """
        self._logger.info("Terminate signaled. Engine will stop after current iteration is finished")
        self.should_terminate = True

    def _run_once_on_dataset(self):
        try:
            start_time = time.time()
            for batch in self.state.dataloader:
                self.state.batch = batch
                self.state.iteration += 1
                self._fire_event(Events.ITERATION_STARTED)
                self.state.output = self._process_function(self, batch)
                self._fire_event(Events.ITERATION_COMPLETED)
                if self.should_terminate:
                    break

            time_taken = time.time() - start_time
            hours, mins, secs = _to_hours_mins_secs(time_taken)
            return hours, mins, secs
        except BaseException as e:
            self._logger.error("Current run is terminating due to exception: %s", str(e))
            self._handle_exception(e)

    def _handle_exception(self, e):
        if Events.EXCEPTION_RAISED in self._event_handlers:
            self._fire_event(Events.EXCEPTION_RAISED, e)
        else:
            raise e

    def run(self, data, max_epochs=1):
        """Runs the process_function over the passed data.

        Args:
            data (Iterable): Collection of batches allowing repeated iteration (e.g., list or DataLoader)
            max_epochs (int, optional): max epochs to run for (default: 1)

        Returns:
            State: output state
        """
        self.state = State(dataloader=data, epoch=0, max_epochs=max_epochs, metrics={})

        try:
            self._logger.debug("Training starting with max_epochs={}".format(max_epochs))
            start_time = time.time()
            self._fire_event(Events.STARTED)
            while self.state.epoch < max_epochs and not self.should_terminate:
                self.state.epoch += 1
                self._fire_event(Events.EPOCH_STARTED)
                hours, mins, secs = self._run_once_on_dataset()
                self._logger.debug("Epoch[%s] Complete. Time taken: %02d:%02d:%02d", self.state.epoch, hours, mins, secs)
                if self.should_terminate:
                    break
                self._fire_event(Events.EPOCH_COMPLETED)

            self._fire_event(Events.COMPLETED)
            time_taken = time.time() - start_time
            hours, mins, secs = _to_hours_mins_secs(time_taken)
            self._logger.debug("Training complete. Time taken %02d:%02d:%02d" % (hours, mins, secs))

        except BaseException as e:
            self._logger.error("Training is terminating due to exception: %s", str(e))
            self._handle_exception(e)

        return self.state
