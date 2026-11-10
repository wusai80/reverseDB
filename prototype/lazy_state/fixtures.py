from collections import UserDict


class RenamedLazy(UserDict):
    def __init__(self,payload,decode):
        super().__init__();self._payload=payload;self._decode=decode

    def __missing__(self,field):
        if field in self._payload:
            result=self._decode(field,self._payload[field])
            self.data[field]=result
            return result
        return None


class ExtraEffect(RenamedLazy):
    def __missing__(self,field):
        self._decode('audit',None)
        return super().__missing__(field)


class EarlyCache(RenamedLazy):
    def __missing__(self,field):
        if field in self._payload:
            self.data[field]=None
            result=self._decode(field,self._payload[field])
            self.data[field]=result
            return result
        return None


class DynamicReceiver(RenamedLazy):
    def __getattribute__(self,name):return super().__getattribute__(name)
