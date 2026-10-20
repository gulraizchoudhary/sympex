from pylsl import StreamInlet, resolve_streams, resolve_streams
import csv
import time
import numpy as np
import matplotlib.pyplot as plt
from matplotlib import patches
import math

# Edit this host_name and data_kinds as your situation
# host_name = ["SeeTrue-Demo2"]
# host_name = ["DESKTOP-MDN17L1"]
host_name = ["968KRQ3"]
data_kinds = ["EyeData", "EyeEvents", "DebugData"]

# only one have to be True
# when 'write_csv' is set to True, it saves data from all streams into a single csv file.
# when 'separate_csv' is set to True, it saves each stream into separate csv files.
write_csv = False
separate_csv = False

# csv info for 'write_csv'
csv_name = f"csv_new_version_{time.time()}.csv"
which_data = []

# csv info for 'separate_csv'
csv_separate_name = f"separate_csv_"
csv_separate_name_list = {}
separate_data = {f"{host}_{name}": [] for host in host_name for name in data_kinds}

# no csv saving
only_parse = {f"{host}_{name}": [] for host in host_name for name in data_kinds}

# how often it saves data into csv
save_time = 50

# print out data analyze
data_analyze = True

# plot eye data
plot_eye_data = True

# Function: pick stream channel name
def pick_ch_name(info):
    ch = info.desc().child('channels').child('channel')
    name_list = []

    for i in range(info.channel_count()):
        name_list.append(ch.child_value("label"))
        ch = ch.next_sibling("channel")

    return name_list

# Function: connect to streams which in host_name and data_kinds
def choose_stream():
    print(f"looking for stream")
    streams = resolve_streams(wait_time=3.)
    devices = {host: {} for host in host_name}

    for stream in streams:
        inlet = StreamInlet(stream)
        host = inlet.info().hostname()
        name = inlet.info().name()

        if host in host_name and name in data_kinds:
            devices[host][name] = inlet
            print(f"Connected to outlet {name}@{host}")
    
    return devices

# Function: save to single csv
def write_to_csv(filename, all_data, headers=None):
    with open(filename, 'a', newline='') as f:
        if headers is None:
            headers = [
                f"{host.replace('-', '_')}_{key}" for host in host_name for key in which_data
            ]
        writer = csv.DictWriter(f, fieldnames=headers)
        if f.tell() == 0:
            writer.writeheader()
        
        max_len = max(len(data_list) for data_list in all_data.values())
        
        for i in range(max_len):
            merged_data = {}
            for host in host_name:
                if i < len(all_data[host]):
                    for key in which_data:
                        merged_data_key = f"{host.replace('-', '_')}_{key}"
                        if key in all_data[host][i]:
                            merged_data[merged_data_key] = all_data[host][i][key]
            writer.writerow(merged_data)

# Function: save to separate csv
def write_separate_csv(filename, data, callname, headers=None):
    with open(filename, 'a', newline='') as f:
        if headers is None:
            headers = separate_data[callname]
        writer = csv.DictWriter(f, fieldnames=headers)
        if f.tell() == 0:
            writer.writeheader()

        try:
            for i in range(len(data)):
                writer.writerow(data[i])
        except:
            pass

# Function: if write_csv is True
def single_csv_funtion(devices, all_data, analyze_data, plot_data):
    for host in host_name:
        plot_data[host] = []
        offset = devices[host][data_kinds[0]].time_correction()
        data, timestamp = devices[host][data_kinds[0]].pull_chunk(max_samples=1024)
        if not data:
            continue

        # for stream except eyeData
        other_data = {name : [] for name in data_kinds}
        other_timeStamp = {name : [] for name in data_kinds}
        other_offset = {name : [] for name in data_kinds}

        # get stream data
        for j in range(len(data_kinds) - 1):
            other_data[data_kinds[j+1]], other_timeStamp[data_kinds[j+1]] = devices[host][data_kinds[j+1]].pull_chunk(max_samples=1024)
            other_offset[data_kinds[j+1]] = devices[host][data_kinds[j+1]].time_correction()

        # for if chunk has more than one time data
        for i in range(len(data)):
            data[i].append(timestamp[i]+offset)
            for j in range(len(data_kinds) - 1):
                if not other_data[data_kinds[j+1]]:
                    for k in range((devices[host][data_kinds[j+1]].info().channel_count()) + 1):
                        data[i].append('NA')
                else:
                    try:
                        data[i].extend(other_data[data_kinds[j+1]][i])
                        data[i].append(other_timeStamp[data_kinds[j+1]][i]+other_offset[data_kinds[j+1]])
                    except IndexError:
                        for k in range((devices[host][data_kinds[j+1]].info().channel_count()) + 1):
                            data[i].append('NA')
            server_data = parse_data(data[i], which_data)

            plot_data[host].append(server_data)

            #save data to all_data for csv
            all_data[host].append(server_data)

            if data_analyze:
                if server_data['eyeEvent'] != 'NA':
                    analyze_data = analyze_data_fun(server_data, analyze_data, host)

    #when all_data has more data amount than save_time, save to csv file
    if all(len(all_data[host]) > save_time for host in host_name):
        limited_data = {namae: data_list[:save_time] for namae, data_list in all_data.items()}
        write_to_csv(csv_name, limited_data)
        for host in host_name:
            del all_data[host][:save_time]

    return all_data, analyze_data, plot_data

# Function: if separate_csv is True, this function will run
def separate_csv_function(devices, separate_all_data, analyze_data, plot_data):
    for host in host_name:
        plot_data[host] = []
        for name in data_kinds:
            offset = devices[host][name].time_correction()
            data, timestamp = devices[host][name].pull_chunk(max_samples=1024)
            if not data:
                continue

            #save all stream to different dict
            for i in range(len(data)):
                data[i].append(timestamp[i] + offset)
                server_data = parse_data(data[i], separate_data[f"{host}_{name}"])
                separate_all_data[f"{host}_{name}"].append(server_data)

                if name == "EyeData":
                    plot_data[host].append(server_data)
                else:
                    temp_data = {f"{key}_{name}": server_data[key] for key in server_data }
                    try:
                        plot_data[host][i] = dict(**plot_data[host][i], **temp_data)
                    except IndexError:
                        pass

                if data_analyze:
                    if name == "EyeEvents":
                        analyze_data = analyze_data_fun(server_data, analyze_data, host)
                        
            if (len(separate_all_data[f"{host}_{name}"]) > save_time):
                write_separate_csv(csv_separate_name_list[f"{host}_{name}"], separate_all_data[f"{host}_{name}"], f"{host}_{name}")
                separate_all_data[f"{host}_{name}"] = []
    return separate_all_data, analyze_data, plot_data

# Function: this is main loop
def receive_seetrue_data(devices):
    print(f"write_csv: {write_csv}")
    print(f"separate_csv: {separate_csv}")
    print(f"data_analyze: {data_analyze}")
    print(f"plot_eye_data: {plot_eye_data}")
    print("Start to get data")
    analyze_data = {host: {"blink_status": False, "blinkB": 0.0, "blinkE": 0.0, "fixation_status": False} for host in host_name}

    all_data = {host: [] for host in host_name}
    separate_all_data = {f"{host}_{name}": [] for host in host_name for name in data_kinds}

    # for plot eye data
    plot_data = {host: [] for host in host_name}
    fig = {host: '' for host in host_name}
    ax = {host: '' for host in host_name}
    for host in host_name:
        fig[host], ax[host] = plt.subplots()
        ax[host].set_title(f"Pupil size and Cornea position in {host}")

    plot_status = {host: False for host in host_name}

    while True:
        if write_csv:
            all_data, analyze_data, plot_data = single_csv_funtion(devices, all_data, analyze_data, plot_data)
        elif separate_csv:
            separate_all_data, analyze_data, plot_data = separate_csv_function(devices,separate_all_data, analyze_data, plot_data)
        else:
            for host in host_name:
                plot_data[host] = []
                for name in data_kinds:
                    offset = devices[host][name].time_correction()
                    data, timestamp = devices[host][name].pull_chunk(max_samples=1024)
                    if not data:
                        continue

                    for i in range(len(data)):
                        data[i].append(timestamp[i] + offset)
                        server_data = parse_data(data[i], only_parse[f"{host}_{name}"])

                        if name == "EyeData":
                            plot_data[host].append(server_data)
                        else:
                            temp_data = {f"{key}_{name}": server_data[key] for key in server_data}
                            try:
                                plot_data[host][i] = dict(**plot_data[host][i], **temp_data)
                            except IndexError:
                                pass

                        if data_analyze:
                            if name == "EyeEvents":
                                analyze_data = analyze_data_fun(server_data, analyze_data, host)
        
        if plot_eye_data:
            plot_status = plot_eye_fun(plot_data, ax, fig, plot_status)


# Function: plot eye data
def plot_eye_fun(plot_data, ax, fig, plot_status):
    for host in host_name:
        try:
            data = plot_data[host][len(plot_data[host]) - 1]
            pupil_diameter_right = 2 * math.sqrt(data['pupilAreaInMM2Right'] / math.pi)
            pupil_diameter_left = 2 * math.sqrt(data['pupilAreaInMM2Left'] / math.pi)
            try:
                cornea_right = [(data['corneaCenterRightX_DebugData']*1000) + 5, (data['corneaCenterRightY_DebugData']*1000)]
                cornea_left = [(data['corneaCenterLeftX_DebugData']*1000) - 5, (data['corneaCenterLeftY_DebugData']*1000)]
            except:
                cornea_right = [5,0]
                cornea_left = [-5,0]
            
            if plot_status[host] is False:
                plot_status[host] = {
                    'right': patches.Circle(xy=(cornea_right[0], cornea_right[1]), radius=pupil_diameter_right/2, color='red', label='Right eye'),
                    'left': patches.Circle(xy=(-cornea_left[0], cornea_left[1]), radius=pupil_diameter_left/2, color='green', label='Left eye')
                }

                ax[host].add_patch(plot_status[host]['right'])
                ax[host].add_patch(plot_status[host]['left'])
                ax[host].legend()
                ax[host].set_xlim([-15, 15])
                ax[host].set_ylim([-15, 15])
            else:
                plot_status[host]['right'].set_radius(pupil_diameter_right)
                plot_status[host]['left'].set_radius(pupil_diameter_left)
                plot_status[host]['right'].set_center(tuple(cornea_right))
                plot_status[host]['left'].set_center(tuple(cornea_left))

            fig[host].canvas.draw()
            plt.pause(0.005)
        except IndexError:
            pass
    
    return plot_status
                
# Function: form data to dict
def parse_data(eye_data, list_of_header):
    server_data = {}
    for i in range(len(list_of_header)):
        try:
            server_data[list_of_header[i]] = float(eye_data[i])
        except:
            server_data[list_of_header[i]] = str(eye_data[i])
    return server_data

# Function: analyze data and show in console log
def analyze_data_fun(server_data, analyze_data, host):
    event_split= server_data['eyeEvent'].split(";")
    eye_event_data = [event_split[1], float(event_split[0])]
    if eye_event_data[0] == " BB" and not analyze_data[host]["blink_status"]:
        print(f"{host}_blink start...")
        analyze_data[host]["BlinkB"] = eye_event_data[1]
        analyze_data[host]["blink_status"] = True
    elif eye_event_data[0] == " BE" and analyze_data[host]["blink_status"]:
        analyze_data[host]["BlinkE"] = eye_event_data[1]
        print(f"{host}_blink end, blink was {analyze_data[host]['BlinkE'] - analyze_data[host]['BlinkB']} ms")
        analyze_data[host]["blink_status"] = False
    elif eye_event_data[0] == " FB" and not analyze_data[host]["fixation_status"]:
        print(f"{host}_fixation start...")
        analyze_data[host]["fixation_status"] = True
    # elif eye_event_data[0][:3] != " FE" and analyze_data[host]["fixation_status"]:
        # print(server_data["PoR.x"], server_data["PoR.y"], server_data["gazePoint.x"], server_data["gazePoint.y"])
    elif eye_event_data[0][:3] == " FE" and analyze_data[host]["fixation_status"]:
        Fx = float(eye_event_data[0][(eye_event_data[0].find('x') + 1) : (eye_event_data[0].find('y') - 1)])
        Fy = float(eye_event_data[0][(eye_event_data[0].find('y') + 1) : (eye_event_data[0].find('d') - 1)])
        Fd = float(eye_event_data[0][(eye_event_data[0].find('d') + 1) :]) * 1000
        print(f"{host}_fixation end, Fixation time was: {Fd}ms, average coordinates were: (x: {Fx}, y: {Fy})")
        analyze_data[host]["fixation_status"] = False
    
    return analyze_data

if __name__ == '__main__':
    # connect to streams
    devices = choose_stream()

    # get channel name for csv header
    for name in data_kinds:
        temp = pick_ch_name(devices[host_name[0]][name].info())
        if name == "EyeData":
            which_data.extend(temp)
        elif name == "EyeEvents":
            if temp == ['']:
                which_data.append('eyeEvent')
            else:
                which_data.extend(temp)
        else:
            for item in temp:
                which_data.append(f"{item}_{name}")
        which_data.append(f'LSLTimeStamp_{name}')
    
    if write_csv:
        headers = [
            f"{host.replace('-', '_')}_{key}" for host in host_name for key in which_data
        ]
        header_data = {header: '' for header in headers}
        write_to_csv(csv_name, header_data, headers=headers)
    elif separate_csv:
        csv_separate_name_list = {f"{host}_{name}": f"{csv_separate_name}{host}_{name}_{time.time()}.csv" for host in host_name for name in data_kinds}
        for host in host_name:
            for name in data_kinds:
                temp = pick_ch_name(devices[host][name].info())
                if name == "EyeEvents":
                    if temp == ['']:
                        separate_data[f"{host}_{name}"].extend(["eyeEvent"])
                    else:
                        separate_data[f"{host}_{name}"].extend(temp)
                else:
                    separate_data[f"{host}_{name}"].extend(temp)
                separate_data[f"{host}_{name}"].append(f'LSLTimeStamp')
                header_data = {header: '' for header in separate_data[f"{host}_{name}"]}
                write_separate_csv(csv_separate_name_list[f"{host}_{name}"], header_data, f"{host}_{name}", headers=separate_data[f"{host}_{name}"])
    else:
        for host in host_name:
            for name in data_kinds:
                temp = pick_ch_name(devices[host][name].info())
                if name == "EyeEvents":
                    if temp == ['']:
                        only_parse[f"{host}_{name}"].extend(["eyeEvent"])
                    else:
                        only_parse[f"{host}_{name}"].extend(temp)
                else:
                    only_parse[f"{host}_{name}"].extend(temp)
                only_parse[f"{host}_{name}"].append(f'LSLTimeStamp')
        
    receive_seetrue_data(devices)