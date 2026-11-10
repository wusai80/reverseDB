class Delegate:
    def __getitem__(self,key):return self.primary(key)


class RenamedContinuation(Delegate):
    def __getitem__(self,name):
        try:
            return super().__getitem__(name)
        except LookupError:
            return self.backup(name)


class BareReraise(Delegate):
    def __getitem__(self,key):
        try:
            return super().__getitem__(key)
        except KeyError:
            self.observe()
            raise


class FinallyBoundary(Delegate):
    def __getitem__(self,key):
        try:
            return super().__getitem__(key)
        except KeyError:
            return self.backup(key)
        finally:
            self.observe()


class RebindingReceiver(Delegate):
    def __getitem__(self,key):
        try:
            return super().__getitem__(key)
        except KeyError as self:
            return self


class ChangedDelegate(Delegate):
    def __getitem__(self,key):
        try:
            return super().another_method(key)
        except KeyError:
            return self.backup(key)


class ReceiverDescriptor:
    def __get__(self,instance,owner):return instance.private


class RecoveryAlbum:
    def __init__(self,events):self.events=events
    def __bool__(self):self.events.append('album_truth');return True
    def load(self):self.events.append('album_load')
    def __getitem__(self,key):self.events.append(['album_field',key]);return 0
