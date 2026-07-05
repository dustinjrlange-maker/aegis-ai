from datetime import timedelta


class FakeClock:
    """Returns a controllable datetime; advance() moves it forward."""
    def __init__(self, start):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t = self.t + timedelta(seconds=seconds)


class RecordingNotifier:
    def __init__(self):
        self.pushes = []

    async def push(self, user_id, title, body, channels):
        self.pushes.append((user_id, title, body, tuple(channels or [])))
