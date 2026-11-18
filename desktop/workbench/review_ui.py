"""Compatibility entry point. All review now lives in MainWindow's workspace."""

class ReviewQueueDialog:
    def __init__(self, owner): self.owner=owner
    def exec(self):
        self.owner.open_review_queue()
        return 0
