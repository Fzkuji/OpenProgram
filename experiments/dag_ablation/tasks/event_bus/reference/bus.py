class EventBus:
    def __init__(self):
        self._subs = {}
        self.errors = []

    def subscribe(self, topic, fn):
        self._subs.setdefault(topic, []).append(fn)
        done = [False]

        def off():
            if done[0]:
                return
            done[0] = True
            try:
                self._subs.get(topic, []).remove(fn)
            except ValueError:
                pass
        return off

    def publish(self, topic, payload):
        self.errors = []
        n = 0
        for fn in list(self._subs.get(topic, [])):
            n += 1
            try:
                fn(payload)
            except Exception as e:
                self.errors.append(e)
        if topic != "*":
            for fn in list(self._subs.get("*", [])):
                n += 1
                try:
                    fn(topic, payload)
                except Exception as e:
                    self.errors.append(e)
        return n
