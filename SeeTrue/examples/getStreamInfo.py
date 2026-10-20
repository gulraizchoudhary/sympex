from pylsl import StreamInlet, resolve_streams

def pick_ch_name(info):
    ch = info.desc().child('channels').child('channel')
    name_list = []

    for i in range(info.channel_count()):
        name_list.append(ch.child_value("label"))
        ch = ch.next_sibling("channel")

    return name_list

streams = resolve_streams(wait_time=3.)
if not streams:
    print("No stream found")
else:
    for stream in streams:
        print(f"name: {StreamInlet(stream).info().name()}")
        print(f"type: {StreamInlet(stream).info().type()}")
        print(f"channel count: {StreamInlet(stream).info().channel_count()}")
        print(f"channel name: {pick_ch_name(StreamInlet(stream).info())}")
        print(f"uid: {StreamInlet(stream).info().uid()}")
        print(f"host_name: {StreamInlet(stream).info().hostname()}")
        #print(StreamInlet(stream).info().as_xml())
