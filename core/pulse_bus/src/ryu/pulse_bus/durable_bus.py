import logging
from typing import Callable

from redis.exceptions import ConnectionError

from ryu.pulse_bus.bus import Subscription
from ryu.pulse_bus.config import DurableBusConfig
from ryu.pulse_bus.pulse import Pulse
from ryu.pulse_bus.replay import PulseReplayer
from ryu.pulse_bus.store import PulseStore
from ryu.pulse_bus.taint import TaintResolver
from ryu.pulse_bus.transport import PulseTransport
from ryu.pulse_bus.validator import PulseValidator

logger = logging.getLogger(__name__)

class DurablePulseBus:
    def __init__(
        self,
        store: PulseStore,
        transport: PulseTransport,
        validator: PulseValidator | None = None,
        config: DurableBusConfig | None = None
    ):
        self.store = store
        self.transport = transport
        self.validator = validator or PulseValidator()
        self.config = config
        self.taint_resolver = TaintResolver(self.store)
        self._subscriptions: list[Subscription] = []
        self._sub_counter = 0

    def publish(self, pulse: Pulse) -> Pulse:
        self.validator.validate(pulse.type, pulse.payload, source=pulse.source)

        resolved_taint = self.taint_resolver.resolve_taint(pulse)
        pulse = Pulse(
            id=pulse.id, space_id=pulse.space_id, type=pulse.type, severity=pulse.severity,
            source=pulse.source, timestamp=pulse.timestamp, payload=pulse.payload,
            taint=resolved_taint, correlation_id=pulse.correlation_id,
            parent_pulse_id=pulse.parent_pulse_id
        )

        # Idempotent append
        self.store.append(pulse)

        try:
            stream_name = f"ryu:pulses:{pulse.space_id}"
            if self.config:
                stream_name = f"{self.config.redis.stream_prefix}:{pulse.space_id}"
            self.transport.publish(pulse, stream_name)
            self.store.mark_published(pulse.id)
        except ConnectionError as e:
            logger.error(f"Failed to publish pulse {pulse.id} to Redis: {e}")

        # Dispatch locally
        for sub in list(self._subscriptions):
            if sub.pulse_type is None or sub.pulse_type == pulse.type:
                sub.callback(pulse)

        return pulse

    def walk_causation(self, pulse_id: str) -> list[Pulse]:
        replayer = self.replayer()
        return replayer.replay_causal_chain(pulse_id)

    def subscribe(
        self, callback: Callable[[Pulse], None], pulse_type: str | None = None
    ) -> Subscription:
        self._sub_counter += 1
        sub = Subscription(
            pulse_type=pulse_type,
            callback=callback,
            subscription_id=f"sub-{self._sub_counter}",
        )
        self._subscriptions.append(sub)
        return sub

    def unsubscribe(self, subscription: Subscription) -> None:
        self._subscriptions = [
            s for s in self._subscriptions if s.subscription_id != subscription.subscription_id
        ]

    def reconcile_unpublished(self, limit: int = 100) -> int:
        pending = self.store.get_unpublished(limit)
        count = 0
        for p in pending:
            try:
                stream_name = f"ryu:pulses:{p.space_id}"
                if self.config:
                    stream_name = f"{self.config.redis.stream_prefix}:{p.space_id}"
                self.transport.publish(p, stream_name)
                self.store.mark_published(p.id)
                count += 1
            except ConnectionError:
                break
        return count

    def replayer(self) -> PulseReplayer:
        return PulseReplayer(self.store)

