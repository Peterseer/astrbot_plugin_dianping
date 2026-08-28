class DianpingError(RuntimeError):
    """Base error shown to the plugin layer."""


class DianpingConfigError(DianpingError):
    pass


class DianpingBlockedError(DianpingError):
    pass


class DianpingParseError(DianpingError):
    pass

