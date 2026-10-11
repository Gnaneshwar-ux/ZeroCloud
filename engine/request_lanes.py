"""Bounded decoding lanes. Slow derivatives never serialize file lookup behind them."""
from concurrent.futures import ThreadPoolExecutor
import threading

class RequestLanes:
    def __init__(self,engine,respond):
        self.engine,self.respond=engine,respond
        self.pools={name:ThreadPoolExecutor(max_workers=count,thread_name_prefix=name) for name,count in [('lookup',2),('view',1),('derivative',1)]}
        # A visible people grid can request sixty tiny crops at once. Lookups
        # do no decoding: keep their bounded queue separate from expensive work.
        self.slots={name:threading.BoundedSemaphore(256 if name=='lookup' else 8) for name in self.pools}
        self.pending=set();self.lock=threading.RLock()

    def submit(self,request):
        op=request['op'];args=request.get('args') or {}
        lane='lookup' if op=='resolve' else 'view' if args.get('foreground') else 'derivative'
        with self.lock:
            if lane=='view':
                # A rapid navigation can replace queued views, never a running decode.
                for future,name in list(self.pending):
                    if name=='view' and not future.running():future.cancel()
            if not self.slots[lane].acquire(blocking=False):
                self.respond({'id':request['id'],'error':'Decoder queue is busy. Try this item again.'});return
            future=self.pools[lane].submit(self.engine.dispatch,op,args)
            self.pending.add((future,lane))
            def done(job):
                try:response={'id':request['id'],'result':job.result()}
                except Exception as error:response={'id':request['id'],'error':str(error) or 'View superseded by a newer selection.'}
                with self.lock:self.pending.discard((job,lane));self.slots[lane].release()
                self.respond(response)
            future.add_done_callback(done)

    def close(self):
        for pool in self.pools.values():pool.shutdown(wait=False,cancel_futures=True)
