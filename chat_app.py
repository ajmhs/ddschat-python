import os
import argparse
import threading
import rti.connextdds as dds
from posixpath import split
from time import sleep
from os import error, path as os_path
import inspect

lock = threading.RLock()
finished = False

def user_subscriber_task(user_reader: dds.DataReader):
    global finished

    status_condition = dds.StatusCondition(user_reader)
    status_condition.enabled_statuses = dds.StatusMask.LIVELINESS_CHANGED

    waitset = dds.WaitSet()
    waitset += status_condition
    
    while not finished:
        active_conditions = waitset.wait(dds.Duration(0.5))
        if status_condition in active_conditions:
            for sample in user_reader.read():
                if sample.info.state.instance_state == dds.InstanceState.NOT_ALIVE_NO_WRITERS and \
                    sample.info.state.sample_state == dds.SampleState.NOT_READ and \
                    not sample.info.valid: # instance "gone" and we haven't read this sample, and there's no data
                        data = user_reader.key_value(sample.info.instance_handle) # get the instance keys from the reader
                        print(f"#Dropped user \"{data['username']}\"")
                        

def message_subscriber_task(message_reader: dds.DataReader):
    global finished

    def process_message(_):
        with lock:
            nonlocal message_reader
            for data, info in message_reader.take():
                if info.valid:
                    print(f"#New chat message from {data['fromUser']},\t Message: \"{data['message']}\"")

    status_condition = dds.StatusCondition(message_reader)
    status_condition.enabled_statuses = dds.StatusMask.DATA_AVAILABLE
    status_condition.set_handler(process_message)

    waitset = dds.WaitSet()
    waitset += status_condition
    while not finished:
        waitset.dispatch(dds.Duration(0.5)) 


def command_task(user, message_writer: dds.DataWriter, user_reader: dds.DataReader):
    global finished

    while not finished:
        command = input('Please enter command: ')

        if command == 'exit' or command == 'quit':
            finished = True
        elif command == 'list':
            with lock:
                for data, info in user_reader.read():
                    if info.valid and info.state.instance_state == dds.InstanceState.ALIVE:
                        print(f"#Username: {data['username']},\t\tGroup: {data['group']}")
        elif command.startswith("send "):
            dest = command.split(maxsplit=2)
            if len(dest) == 3:
                msg = dds.DynamicData(chat_message_t)
                msg['fromUser'] = user
                msg['toUser'] = dest[1]
                msg['toGroup'] = dest[1]
                msg['message'] = dest[2]
                
                with lock:
                    message_writer.write(msg)
            else:
                print('Invalid usage. Use "send user|group message"\n')
        else:
            print('Unknown command')


file_path = os_path.dirname(os_path.realpath(__file__))

parser = argparse.ArgumentParser(description='DDS Chat Application')

parser.add_argument('user', help='User Name', type=str)
parser.add_argument('group', help='Group Name', type=str)
parser.add_argument('-f', '--firstname', help='First Name', type=str, default='')
parser.add_argument('-l', '--lastname', help='Last Name', type=str, default='')

args = parser.parse_args()

os.environ['user'] = str(args.user)
os.environ['group'] = str(args.group)

config_name="Chat_ParticipantLibrary::ChatParticipant"

provider = dds.QosProvider(uri=os_path.join(file_path, 'Chat.xml'))
participant = provider.create_participant_from_config(config=config_name) 

# Types
chat_user_t = provider.type('ChatUser')
chat_message_t = provider.type('ChatMessage')

# Writers
user_writer = dds.DynamicData.DataWriter(
    participant.find_datawriter('ChatUserPublisher::ChatUser_Writer'))
message_writer = dds.DynamicData.DataWriter(
    participant.find_datawriter('ChatMessagePublisher::ChatMessage_Writer'))

# Readers
user_reader = dds.DynamicData.DataReader(
    participant.find_datareader('ChatUserSubscriber::ChatUser_Reader'))
message_reader = dds.DynamicData.DataReader(
    participant.find_datareader('ChatMessageSubscriber::ChatMessage_Reader'))

# Register user
user = dds.DynamicData(chat_user_t)
user['group'] = args.group
user['username'] = args.user
if args.firstname:
    user['firstName'] = args.firstname
if args.lastname:
    user['lastName'] = args.lastname

hinst = user_writer.register_instance(user)

t1 = threading.Thread(target=command_task, args=(args.user, message_writer, user_reader,))
t1.start()

t2 = threading.Thread(target=message_subscriber_task, args=(message_reader,))
t2.start()

t3 = threading.Thread(target=user_subscriber_task, args=(user_reader, ))
t3.start()

user_writer.write(user)

t1.join()
t2.join()
t3.join()

# Unregister user
user_writer.unregister_instance(hinst)
