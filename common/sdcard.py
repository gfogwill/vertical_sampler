import board
import sdcardio
import storage
import json


class SDCard:
    def __init__(self, spi, fname: str):
        cs = board.GP18
        sdcard = sdcardio.SDCard(spi, cs)
        vfs = storage.VfsFat(sdcard)
        storage.mount(vfs, "/sd")
        self.fname = fname

    def write(self, s: str):
        print(s)
        with open("/sd/{}".format(self.fname), "a") as f:
            f.write(s)

    def write_dict(self, d: dict, fname):
        print(d)
        import json
        json_str = json.dumps(d)
        with open("/sd/{}".format(fname), "a") as f:
            f.write(json_str + "\n")
