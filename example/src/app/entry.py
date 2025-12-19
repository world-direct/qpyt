import utime # type: ignore

def run():
    print("Entry point")

    i = 0
    while True:
        print("Looping.... #%d %s" % (i, str(utime.time())))
        utime.sleep(1)
        i += 1
        