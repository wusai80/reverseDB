"""Structural controls for the source transformer, independent of beets names."""
class Base:
    def populate(self, values):
        for key, value in values.items():
            self[key] = value


class Renamed(Base):
    def populate(self, values):
        super().populate(values)
        if self.checkpoint == 0 and "checkpoint" in values:
            self.checkpoint = values["checkpoint"]


class ExtraCall(Base):
    def populate(self, values):
        super().populate(values)
        if self.inspect() and "checkpoint" in values:
            self.checkpoint = values["checkpoint"]


class ReachableBody(Base):
    def populate(self, values):
        super().populate(values)
        if self.checkpoint == 0 or "checkpoint" in values:
            self.checkpoint = 1


class Resource:
    def __init__(self, events, mode='normal'):
        self.events = events
        self.mode = mode

    def __bool__(self):
        self.events.append('resource_truth')
        if self.mode == 'truth-error':
            raise RuntimeError('resource truth failure')
        return self.mode != 'false'

    @property
    def revision(self):
        self.events.append('resource_revision')
        if self.mode == 'revision-error':
            raise RuntimeError('resource revision failure')
        return 7


class Truth:
    def __init__(self, events, mode):
        self.events, self.mode = events, mode

    def __bool__(self):
        self.events.append('predicate_truth')
        if self.mode == 'truth-error':
            raise RuntimeError('predicate truth failure')
        return self.mode != 'false'


class Comparable:
    def __init__(self, events, mode):
        self.events, self.mode = events, mode

    def __eq__(self, value):
        self.events.append(['predicate_eq', value])
        if self.mode == 'eq-error':
            raise RuntimeError('predicate comparison failure')
        return Truth(self.events, self.mode)


class ExtraBase(Base):
    def populate(self, values):
        self.checkpoint
        for key, value in values.items():
            self[key] = value


class ExtraBaseChild(ExtraBase):
    def populate(self, values):
        super().populate(values)
        if self.checkpoint == 0 and 'checkpoint' in values:
            self.checkpoint = 1


class Inherited(Renamed):
    pass


class PublicState:
    def reset(self):
        self.dirty = set()


class ResourceWrite:
    def reset(self):
        self._db.revision = set()


class EscapingState:
    def reset(self):
        self._db = self


class ExternalState:
    def reset(self):
        self._dirty = external


class LocalRebinding:
    def reset(self):
        set = self._db
        self._dirty = set()


class RenamedState:
    def reset(self):
        self._touched = set()
        if self._owner:
            self._epoch = self._owner.revision
