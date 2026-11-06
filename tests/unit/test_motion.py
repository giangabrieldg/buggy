class TimerRoot:
    def __init__(self):
        self.pending = {}
        self.next_id = 0

    def after(self, delay_ms, callback):
        self.next_id += 1
        timer_id = f"after-{self.next_id}"
        self.pending[timer_id] = (delay_ms, callback)
        return timer_id

    def after_cancel(self, timer_id):
        self.pending.pop(timer_id, None)

    def fire(self, timer_id):
        _delay, callback = self.pending.pop(timer_id)
        callback()


def test_cancel_all_prevents_pending_motion_callbacks():
    from personal_assistant.motion import MotionScheduler

    root = TimerRoot()
    motion = MotionScheduler(root)
    observed = []
    motion.schedule(150, observed.append, "reveal")
    motion.schedule(400, observed.append, "neutral")

    motion.cancel_all()

    assert root.pending == {}
    assert observed == []


def test_completed_motion_is_not_cancelled_again_and_close_stops_new_work():
    from personal_assistant.motion import MotionScheduler

    root = TimerRoot()
    motion = MotionScheduler(root)
    observed = []
    timer_id = motion.schedule(150, observed.append, "shown")
    root.fire(timer_id)

    motion.close()

    assert observed == ["shown"]
    assert motion.schedule(150, observed.append, "late") is None
    assert root.pending == {}


def test_one_motion_can_be_cancelled_without_affecting_other_motion():
    from personal_assistant.motion import MotionScheduler

    root = TimerRoot()
    motion = MotionScheduler(root)
    observed = []
    first = motion.schedule(150, observed.append, "first")
    second = motion.schedule(150, observed.append, "second")

    motion.cancel(first)
    root.fire(second)

    assert observed == ["second"]
