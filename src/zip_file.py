
import typing
import zipstream
import math as m
import time


TG_MAX_FILE_SIZE = 2000*1024*1024


class Reader(typing.BinaryIO):
    def write(self, s: typing.Union[bytes, bytearray]) -> int:
        pass

    def mode(self) -> str:
        pass

    def name(self) -> str:
        pass

    def close(self) -> None:
        pass

    def closed(self) -> bool:
        pass

    def fileno(self) -> int:
        pass

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        pass

    def readable(self) -> bool:
        pass

    def readline(self, limit: int = -1) -> typing.AnyStr:
        pass

    def readlines(self, hint: int = -1) -> typing.List[typing.AnyStr]:
        pass

    def seek(self, offset: int, whence: int = 0) -> int:
        pass

    def seekable(self) -> bool:
        pass

    def tell(self) -> int:
        pass

    def truncate(self, size: int = None) -> int:
        pass

    def writable(self) -> bool:
        pass

    def write(self, s: typing.AnyStr) -> int:
        pass

    def writelines(self, lines: typing.List[typing.AnyStr]) -> None:
        pass

    def __enter__(self) -> 'typing.IO[typing.AnyStr]':
        pass

    def __exit__(self, type, value, traceback) -> None:
        pass


class ZipTorrentContentFile(Reader):
    def __init__(self, file_iter, name, size):
        self.buf = bytes()
        self.processed_size = 0
        # self.progress_text = None
        self.files_size_sum = 0
        file_names_sum = 0
        self.zipstream = zipstream.ZipFile(mode='w', compression=zipstream.ZIP_STORED, allowZip64=True)
        self.zipstream.write_iter(name, file_iter)
        self.files_size_sum += size if size != 0 else 100 * 1024 * 1024 * 1024
        file_names_sum += len(name.encode('utf'))

        #self.real_size = 21438417 + 205 + 6 #len(files) * (30 + 16 + 46) + 2 * file_names_sum + files_size_sum + 22 + 512
        self.real_size = (30 + 16 + 46) + 2 * file_names_sum + self.files_size_sum + 22 + 5120

        self.big = self.real_size > TG_MAX_FILE_SIZE
        self._size = TG_MAX_FILE_SIZE if self.big else self.real_size

        last_repl = False
        f_name = ''
        for i in name:
            if not i.isalnum():
                f_name += '_' if last_repl == False else ''
                last_repl = True
            else:
                f_name += i
                last_repl = False

        self._name = f_name
        self.zip_num = 1
        self.must_next_file = False
        self.zip_parts = m.ceil(self.real_size / TG_MAX_FILE_SIZE)
        self.downloaded_bytes_count = 0
        self.last_percent = -1
        self.should_close = False
        self.zipiter = self.zipstream.__aiter__()
        self.is_finished = False
        self.last_progress_update = time.time()


    @property
    def size(self):
        if self.big:
            data_left = self.real_size - (self.zip_num - 1) * TG_MAX_FILE_SIZE
            if data_left > TG_MAX_FILE_SIZE:
                return TG_MAX_FILE_SIZE
            else:
                return data_left
        else:
            return self._size

    def close(self):
        self.zipstream.close()

    def closed(self):
        return False

    def __enter__(self):
        pass

    def __exit__(self):
        pass

    def flush(self):
        pass

    def isatty(self):
        return False

    def readable(self):
        return True

    def readline(self, size=-1):
        # future_data = asyncio.run_coroutine_threadsafe(self.read(), client.loop)
        # data = future_data.result()
        return None

    def readlines(self, hint=-1):
        # future_data = asyncio.run_coroutine_threadsafe(self.read(), client.loop)
        # data = future_data.result()
        return None

    def seekable(self):
        return False

    def tell(self):
        return 0

    def writable(self):
        return False

    def writelines(self, lines):
        return

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.must_next_file:
            self.must_next_file = False
            raise StopAsyncIteration

        data = await self.read(512*1024)
        if len(data) == 0 or self.processed_size == 0:
            raise StopAsyncIteration
        return data

    @property
    def name(self):
        if self.big:
            return self._name[:20]+'.zip'+'.{:03d}'.format(self.zip_num)
        else:
            return self._name + '.zip'

    async def read(self, n=-1):
        resp = bytes()
        if len(self.buf) != 0:
            resp = self.buf
            self.buf = bytes()
        if n == -1:
            n = self.size
        if n + self.processed_size > TG_MAX_FILE_SIZE:
            n = TG_MAX_FILE_SIZE - self.processed_size
        elif n + self.processed_size > self.size:
            n = self.size - self.processed_size

        async for data in self.zipiter:
            if data is None:
                break
            resp += data
            if not (len(resp) < n and self.processed_size < TG_MAX_FILE_SIZE):
                break

                #if time.time() - self.last_progress_update > 2:
                #    await self.event.edit(self.progress_text.format(str(m.floor((self.downloaded_bytes_count*100) / self.size))))
                #    self.last_progress_update = time.time()
                #resp += await self.zipstream.__aiter__().__next__()
                #if len(resp) == 0 and self.should_close == False:
                #    print("\nSHOULD CLOSE CALL\n")
                #    self.zipiter = iter(self.zipstream)
                #    self.should_close = True
                #    continue
        if len(resp) > n:
            self.buf = resp[n:]
            resp = resp[0:n]

        if len(resp) != 0 and n == 0:
            # send last piece
            self.processed_size += len(resp)
            return resp

        self.processed_size += len(resp)

        if self.processed_size >= TG_MAX_FILE_SIZE:
            #if self.is_finished == False and self.real_size - TG_MAX_FILE_SIZE <= 0:
            #    self.real_size += 1024
            #    TG_MAX_FILE_SIZE = TG_MAX_FILE_SIZE if self.should_split else self.real_size
            #    self.big = self.real_size > TG_MAX_FILE_SIZE
            #    self.size = TG_MAX_FILE_SIZE if self.big else self.real_size
            #else:
            self.processed_size = 0
            self.must_next_file = True
            #self.real_size -= TG_MAX_FILE_SIZE
            # self._size = TG_MAX_FILE_SIZE if self.real_size > TG_MAX_FILE_SIZE else self.real_size

        return resp
