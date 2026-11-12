import asyncio, sys
from shazamio_core import Recognizer
async def run(p):
    s = await Recognizer().recognize_path(value=p)
    return s.signature.uri, s.signature.samples
for p in sys.argv[1:]:
    uri, ms = asyncio.run(run(p))
    open(p + '.uri', 'w').write(uri + '\n'); print(p, ms, len(uri))
