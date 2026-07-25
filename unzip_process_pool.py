import queue
import time
from enum import Enum
from multiprocessing import Process, Manager


class Prefix(Enum):
    PASSWORD_COLLISION = 'PC_'
    UNZIP = 'unzip_'


def _worker_loop(queue, result_queue):
    while True:
        item = queue.get()
        if item is None:
            break
        list_id, func, args, kwargs = item
        try:
            result = func(*args, **kwargs)
        except Exception as e:
            result = 6, e
            result_queue.put((list_id, result))
        else:
            result_queue.put((list_id, result))


class ProcessResourceManager:
    def __init__(self, max_queue_size: int):
        self.manager = Manager()
        self.task_queue = self.manager.Queue(maxsize=max_queue_size)
        self.result_queue = self.manager.Queue()
        self.log_queue = self.manager.Queue()
        self.lock = self.manager.Lock()
        self.list_counters = self.manager.dict()
        self.list_progress = self.manager.dict()
        self.list_events = self.manager.dict()
        self.list_status = self.manager.dict()
        self.list_cancelled = self.manager.dict()
        self._closed = False

    def submit(self, list_id, func, *args, **kwargs):
        with self.lock:
            if self.list_cancelled.get(list_id):
                return
        self.task_queue.put((list_id, func, args, kwargs), block=True)

    def cancel_list(self, list_id: str):
        """标记列表已失败/取消：忽略后续 worker 结果并唤醒等待方。"""
        with self.lock:
            self.list_cancelled[list_id] = True
            if list_id in self.list_events:
                self.list_events[list_id].set()

    def set_list_total(self, list_id: str, total: int):
        """手动设置该列表的总任务数（需在提交任务前调用）"""
        with self.lock:
            self.list_counters[list_id] = total
            self.list_progress[list_id] = 1
            self.list_events[list_id] = self.manager.Event()
            self.list_status[list_id] = {'completed': False, 'error': None}
            self.list_cancelled[list_id] = False

    def shutdown(self):
        """关闭 multiprocessing.Manager；可重复调用。"""
        if self._closed:
            return
        self._closed = True
        self.manager.shutdown()


class ProcessPool:
    def __init__(self, max_workers: int, resource_manager: ProcessResourceManager):
        self.resource = resource_manager
        self.workers = []
        self._closed = False

        self.result_processor = Process(
            target=_monitor,
            args=(
                self.resource.result_queue, self.resource.lock, self.resource.list_counters,
                self.resource.list_progress, self.resource.list_events,
                self.resource.list_status, self.resource.log_queue, self.resource.list_cancelled),
            daemon=True
        )
        self.result_processor.start()

        for _ in range(max_workers):
            worker = Process(
                target=_worker_loop,
                args=(self.resource.task_queue, self.resource.result_queue),
                daemon=False
            )
            worker.start()
            self.workers.append(worker)

    def wait_all(self):
        while True:
            with self.resource.lock:
                if self.resource.result_queue.empty():
                    break
            time.sleep(0.1)

    def shutdown(self, wait=True):
        """关闭所有子进程；异常退出时 wait=False 可立即终止工作进程。"""
        if self._closed:
            return
        self._closed = True
        if wait:
            self.wait_all()
            for _ in range(len(self.workers)):
                self.resource.task_queue.put(None)
            for worker in self.workers:
                worker.join()
        else:
            for worker in self.workers:
                if worker.is_alive():
                    worker.terminate()
            for worker in self.workers:
                worker.join(timeout=5)
        if self.result_processor.is_alive():
            self.resource.result_queue.put(None)
            self.result_processor.join(timeout=5)
        if self.result_processor.is_alive():
            self.result_processor.terminate()
            self.result_processor.join(timeout=5)


def _monitor(result_queue, lock, list_counters, list_progress, list_events, list_status, log_queue, list_cancelled):
    while True:
        try:
            item = result_queue.get(timeout=0.5)
            if item is None:
                break
            list_id, result = item
            count_result(result, list_counters, list_events, list_id, list_status, lock, list_progress, log_queue, list_cancelled)
        except queue.Empty:
            pass


def count_result(result, list_counters, list_events, list_id: str, list_status, lock, progress, log_queue, list_cancelled):
    if list_cancelled.get(list_id):
        return
    total = list_counters.get(list_id, 0)
    if list_id.startswith(Prefix.UNZIP.value):
        code, msg = result
        if code == 0:
            with lock:
                if list_cancelled.get(list_id):
                    return
                progress[list_id] += 1
                current = progress[list_id]
                if current >= total:
                    list_status[list_id] = {'completed': True, 'error': None}
                    list_events[list_id].set()
            log_queue.put((list_id, f"解压中 {current}/{total}"))
        else:
            with lock:
                list_cancelled[list_id] = True
                list_status[list_id] = {'completed': False, 'error': msg}
                list_events[list_id].set()
            log_queue.put((list_id, f"解压失败 0/{total}"))
    elif list_id.startswith(Prefix.PASSWORD_COLLISION.value):
        if result:
            list_status[list_id] = {'completed': True, 'error': False, 'password': result}
        else:
            list_status[list_id] = {'completed': True, 'error': 'NO PASSWORD'}
