from . import Field, Source


class PushApi(Source):
    id = "api"
    label = "API"
    description = "You send items to WatchFox over HTTP. Nothing is polled."
    interval = None
    fields = []
