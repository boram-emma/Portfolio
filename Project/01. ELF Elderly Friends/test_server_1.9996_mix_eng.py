

# %%
from datetime import datetime, timedelta
from datetime import time as dt
from pytz import timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from typing import List, Dict
from openai import OpenAI
from dotenv import load_dotenv, find_dotenv

import pymysql
import base64
import requests
import wave
import json
import os

# database config information
db_config = {
    'host': '127.0.0.1',
    'user': 'root',
    'password': '!@khj!@84627913!@',
    'database': 'seniorcare'
}


# Load environment variables from a .env file
_ = load_dotenv(find_dotenv()) # Load the .env file

# Initialize the OpenAI client with the API key
openai_client = OpenAI(
    api_key=os.environ['OPENAI_API_KEY'],  # Retrieves API key from environment variables
)


# Import user information from database
class UserInfo:
    def __init__(self, db_config):
        self.db_config = db_config

        # get total raw userinformation from mysql db using phone_id
    def get_total_userinfo_from_db(self, phone_id):
        try:
            # Connection with MySQL database
            connection = pymysql.connect(**self.db_config)
            cursor = connection.cursor()
            
            query = """SELECT 
                            user.phone_id, 
                            user.username, 
                            user.userid, 
                            user.usersex, 
                            user.userage,
                            healthinfo.disease,
                            alarm.casual_alarm_time, 
                            healthinfo.medication,
                            healthinfo.injection, 
                            healthinfo.healthissue,
                            summarization.conversation_start,
                            summarization.summary
                        FROM 
                            user 
                        JOIN 
                            alarm ON user.phone_id = alarm.phone_id 
                        JOIN 
                            healthinfo ON user.phone_id = healthinfo.phone_id
                        JOIN 
                            summarization ON user.phone_id = summarization.phone_id
                        WHERE 
                            user.phone_id = %s
                            AND summarization.session_id = (
                                SELECT session_id 
                                FROM summarization 
                                WHERE phone_id = %s 
                                ORDER BY summ_created_at DESC 
                                LIMIT 1
                            );"""
            values = (phone_id, phone_id)
            
            cursor.execute(query, values)
            total_userinfo_db = cursor.fetchall()

        except pymysql.Error as error:
            print(f"Error while connecting to MySQL: {error}")
            total_userinfo_db = None

        finally:
            cursor.close()
            connection.close()
            
        return total_userinfo_db

    # switch string time from db to datetime.time() type  
    def parse_time(self, time_str):
        return datetime.strptime(time_str, "%H:%M").time()

    # convert to utc time because of app time 
    def convert_to_utc_time(self, time_obj):
        # korean_time = datetime.combine(datetime.today(), time_obj).time()
        utc_time = (datetime.combine(datetime.today(), time_obj) - timedelta(hours=9)).time()
        return utc_time

    # preprocessing alarm time information imported from database
    def process_alarms(self, alarm_json_str):
        # if alarm_json_str in (None, "없음"):
        #     return "없음"
        
        alarms = json.loads(alarm_json_str)
        if isinstance(alarms, list):
            for alarm in alarms:
                for key, value in alarm.items():
                    if isinstance(value, list):
                        alarm[key] = [self.convert_to_utc_time(self.parse_time(t)) for t in value]
                    elif isinstance(value, dict):
                        for med, times in value.items():
                            value[med] = [self.convert_to_utc_time(self.parse_time(t)) for t in times]
        return alarms
    
    # preprocessing medication information imported from database
    def get_medication_name(self, medication_json_str):
        # if medication_json_str in (None, "없음"):
        #     return "없음"
        
        medications = json.loads(medication_json_str)
        user_medication = []
        if isinstance(medications, list):
            for med_dict in medications:
                for med in med_dict.keys():
                    user_medication.append(med)
        return user_medication
    
    # preprocessing injection information imported from database
    def get_injection_name(self, injection_json_str):
        # if injection_json_str in (None, "없음"):
        #     return "없음"
        
        injections = json.loads(injection_json_str)
        user_injection = []
        if isinstance(injections, list):
            for inj_dict in injections:
                for inj in inj_dict.keys():
                    user_injection.append(inj)
        return user_injection
    
    # get total preprocessed user information and make user_info dictionary data
    def get_user_info(self, phone_id):
        data = self.get_total_userinfo_from_db(phone_id)
        if not data:
            return None
        
        user_info = {
            "phone_id": data[0][0],
            "username": data[0][1],
            "userid": data[0][2],
            "usersex": data[0][3],
            "userage": data[0][4],
            "disease": data[0][5] if "null" not in data[0][5] else [],
            "casualalarm": self.process_alarms(data[0][6]) if "null" not in data[0][6] else [],
            "medication": self.get_medication_name(data[0][7]) if "null" not in data[0][7] else [],
            "health_med_alarm": self.process_alarms(data[0][7]) if "null" not in data[0][7] else [],
            "injection": self.get_injection_name(data[0][8]) if "null" not in data[0][8] else [],
            "health_inj_alarm": self.process_alarms(data[0][8]) if "null" not in data[0][8] else [],
            "healthissue": data[0][9] if data[0][9] is not None else [],
            "prev_conversation_start": data[0][10] if "null" not in data[0][10] else [], #mix
            "prev_summary": data[0][11] if "null" not in data[0][11] else [] #mix
        }
        
        return user_info


# To prevent the mixing of user information that may occur when importing multiple users’ information simultaneously, 
# it is necessary to manage user information by session. This can be achieved using the UserSession class.
class UserSession:
    
    def __init__(self, phone_id, data):
        self.phone_id = phone_id
        self.data = data
        self.time_delta = timedelta(minutes=3)
        self.current_time = datetime.now(timezone('Asia/Seoul')).time()
        self.close_time, self.close_key = self.check_times()
        self.session_id = self.generate_session_id()
        self.sessiontime = self.get_sessiontime()
        self.current_timearea = self.get_current_timearea()
        self.context_counter = 0
        self.context = []
        self.context_string = []

    def generate_session_id(self):
        current_time_str = datetime.now(timezone('Asia/Seoul')).strftime('%Y%m%d%H%M%S')
        session_id = f"{current_time_str}_{self.phone_id}"
        return session_id

    def is_within_time_range(self, target_time, reference_time, delta):
        reference_datetime = datetime.combine(datetime.today(), reference_time)
        target_datetime = datetime.combine(datetime.today(), target_time)
        return abs(reference_datetime - target_datetime) <= delta
    
    def add_hours_to_time(self, time_obj, hours):
        full_datetime = datetime.combine(datetime.today(), time_obj)
        new_datetime = full_datetime + timedelta(hours=hours)
        return new_datetime.time()

    def check_times(self):
        close_time = None
        close_key = None
        for key, value in self.data.items():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        for sub_key, times in item.items():
                            for t in times:
                                t_with_added_hours = self.add_hours_to_time(t, 9)
                                if self.is_within_time_range(t_with_added_hours, self.current_time, self.time_delta):
                                    close_time = t_with_added_hours
                                    close_key = key
                    elif isinstance(item, list):
                        for t in item:
                            t_with_added_hours = self.add_hours_to_time(t, 9)
                            if self.is_within_time_range(t_with_added_hours, self.current_time, self.time_delta):
                                close_time = t_with_added_hours
                                close_key = key
        return close_time, close_key

    # get conversation sessiontime which will be saved in database to distinguish conversation session
    def get_sessiontime(self):
        session_datetime = datetime.strptime(self.session_id.split('_')[0], '%Y%m%d%H%M%S')
        sessiontime = session_datetime.time().replace(second=0, microsecond=0)
        sessiontime = dt(sessiontime.hour, sessiontime.minute)  # Only keep hour and minute
        return sessiontime
    
    # decide session timearea using sessiontime 
    def get_current_timearea(self):
        if datetime.strptime("06:00", "%H:%M").time() <= self.sessiontime <= datetime.strptime("11:59", "%H:%M").time():
            current_timearea = "아침"
        elif datetime.strptime("12:00", "%H:%M").time() <= self.sessiontime <= datetime.strptime("17:59", "%H:%M").time():
            current_timearea = "점심"
        elif datetime.strptime("18:00", "%H:%M").time() <= self.sessiontime <= datetime.strptime("23:59", "%H:%M").time():
            current_timearea = "저녁"
        else:
            current_timearea = "새벽"
        return current_timearea

# %%
def get_transcript(file_path): # Speech to Text
    # Open the audio file in binary read mode
    audio_file = open(file_path, "rb")
    
    # Use the OpenAI Whisper model to transcribe the audio
    transcript = openai_client.audio.transcriptions.create(
        model="whisper-1",           # Specifies the Whisper model to use
        file=audio_file,             # Passes the audio file to the API
        response_format="text"       # Requests the transcription in text format
    )

    # Return the transcription
    return transcript

# %%

# insert conversation to context table in database
def save_context_to_db(session, context_counter, user_info, role, created_time, content, conversation_start=None, conv_model=None):

    phone_id = session.phone_id
    session_id = session.session_id
    username = user_info['username']

    try:
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor()

        query = """
            INSERT INTO context (session_id, unique_number, created_at, phone_id, username, conversation_start, model, role, content) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        values = (session_id, context_counter, created_time, phone_id, username, conversation_start, conv_model, role, content)

        cursor.execute(query, values)
        connection.commit()

        print("Context saved to the database.")

    except pymysql.Error as error:
        print(f"Error while connecting to MySQL: {error}")

    finally:
        cursor.close()
        connection.close()

    # to save user speech audiofile and make name of the audiofiles
    if role == "user":
        userevenno = context_counter
        return userevenno

# %%
# save name of audiofile to context table in database
def save_audiodir_to_context(session, audiodir):
    session_id = session.session_id
    userevenno = session.context_counter -1
    
    try:
        
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor()

        
        query = """UPDATE context
                    SET audio_file_dir = %s
                    WHERE session_id = %s AND unique_number = %s AND role = 'user';"""
        values = (audiodir, session_id, userevenno)

        
        cursor.execute(query, values)
        connection.commit()

        print("Audio directory saved to the database.")

    except pymysql.Error as error:
        print(f"Error while connecting to MySQL: {error}")

    finally:
        cursor.close()
        connection.close()

# %%

def add_hours_to_time(t, hours):
    full_datetime = datetime.combine(datetime.today(), t)
    new_datetime = full_datetime + timedelta(hours=hours)
    return new_datetime.time()

def adjust_times(alarm_time, hours):
    
    if alarm_time == []:
        korea_alarm_time = []
    else:
        korea_alarm_time = []
        for med in alarm_time:
            new_med = {}
            for key, times in med.items():
                new_times = [add_hours_to_time(t, hours) for t in times]
                new_med[key] = new_times
            korea_alarm_time.append(new_med)
    return korea_alarm_time


# get medication alarm greeting message
def med_regular_greeting(session, user_info):
    context = session.context

    korea_medication_alarm = adjust_times(user_info['health_med_alarm'], 9)
    korea_injection_alarm = adjust_times(user_info['health_med_alarm'], 9)

    prompt = f"""The assistant greets the user based on the given user info and user health information.
              The assistant should talk to the elderly like a friendly neighbor.
              The assistant uses casual and informal conversation style.
              The assistant is talking to the elderly who wants to have friendly conversation. 
              The assistant should not use difficult words or phrases, and should be patient and understanding.
              The assistant speaks only English and is designed to help the elderly who can understand only english.

                The assistant should greet the user based on the given user personal information and user health information.
                if current time is within the user's medication time or injection time, the assistant should ask the user whether the user have a medicine or injection.
                - User information 
                    username : {user_info['username']}, user gender : {user_info['usersex']}, user age : {user_info['userage']}
                - User health information :
                    The user's illnesses or diseases : {json.loads(user_info['disease']) if user_info['disease'] else []}
                    The medication that the user must take and the times the users need to take it, and assistant should ask the user whether the user have a medicine : {korea_medication_alarm}
                    The injection medication that the user must inject themselves and the times the users need to take it, and the assistant should ask the user whether the user have an injection : {korea_injection_alarm}
                    Current time : {session.sessiontime}
                    If the current time is within 3 minutes of the user’s medication time or injection time, the assistant should briefly ask the user whether they have taken their medicine or injection, including a greeting message.
                    The user's health issues about which the assistant sometimes ask a question : {user_info['healthissue']}"""
        
    userhiddengreeting = [{"role": "user", "content": "Hello!" }]


    greetingresponse = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role":"system", "content": prompt}] + userhiddengreeting,
        max_tokens=1024,
        temperature=0.5,
        stop=["\n"]
    )
    
    response_text = greetingresponse.choices[0].message.content
    greeting_time = datetime.now(timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S')
    conv_model = greetingresponse.model
    conversation_start = "med_regular_greeting"

    context.append({"role": "assistant", "content": response_text})
    session.context_counter += 1  # Increment context_counter
    context_counter = session.context_counter
    
    save_context_to_db(session, context_counter, user_info, "assistant", greeting_time, response_text, conversation_start, conv_model)

    return response_text  



# select greeting message from summarization table, which is usually used for casual alarm time
def get_greeting_from_summarization(session, user_info):
    context = session.context
    phone_id = session.phone_id
  
    try:
        
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor()

        # summarization 테이블에서 다음 질문을 가져오는 쿼리
        query = """SELECT next_first_question FROM summarization 
                    WHERE phone_id = %s AND session_id = (SELECT session_id
                                                        FROM summarization WHERE phone_id = %s
                                                        ORDER BY summ_created_at DESC
                                                        LIMIT 1
                                                        );"""
        values = (phone_id, phone_id)
        
        
        cursor.execute(query, values)
        first_question_from_summ = cursor.fetchone()

        print("Selected next first question from summarization table.")
        print(f"phone_id : {phone_id}\nfirst_question_from_summ : {first_question_from_summ}")

    except pymysql.Error as error:
        print(f"Error while connecting to MySQL: {error}")

    finally:
        cursor.close()
        connection.close()
        
    greetingresponse = "".join(first_question_from_summ)
    greeting_text = greetingresponse
    greeting_time = datetime.now(timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S')
    conv_model = "summarization"
    conversation_start = "get_greeting_from_summarization"

    context.append({"role": "assistant", "content": greeting_text})
    session.context_counter += 1  # Increment context_counter
    context_counter = session.context_counter

    save_context_to_db(session, context_counter, user_info, "assistant", greeting_time, greeting_text, conversation_start, conv_model)

    return greeting_text
  

# make greeting message for morning casual alarm, which inform Chuncheon's weather information
def make_weather_greeting(session, user_info):
    # global context
    # global username
    # global sessiontime
    context = session.context
  
    temp_time = datetime.now(timezone('Asia/Seoul'))
    mod_datetime = temp_time - timedelta(hours = 0.5)
    input_date = mod_datetime.strftime('%Y%m%d%H%M')[:-4]
    input_time = mod_datetime.strftime('%Y%m%d%H%M')[-4:]

    # Chuncheon coordinate
    nx = '73'
    ny = '134'
    
    # serviceKey for weather api from 공공데이터포털(data.go.kr) 
    serviceKey = "8SukIEKtVXQQ3NeKhumtUS3gof1NZbvkVyP6G6dzGAhc4kR8PHImAvQf3l5yadry8iDYX0MZf4MMvMIsm7hqoA%3D%3D"

    # url
    url = f"http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtFcst?serviceKey={serviceKey}&numOfRows=60&pageNo=1&dataType=json&base_date={input_date}&base_time={input_time}&nx={nx}&ny={ny}"


    response = requests.get(url, verify=False)
    weather_result = json.loads(response.text)

    weather_result_append = []
    target_fcst_time = weather_result["response"]["body"]["items"]["item"][0]["fcstTime"]

    for item in weather_result["response"]["body"]["items"]["item"]:
        if item["fcstTime"] == target_fcst_time:
            weather_result_append.append(item)

    mod_result = []

    category_values = {}
    for item in weather_result_append:
        category_values[item['category']] = item['fcstValue']
        base_data = {
            'baseDate': item['baseDate'],
            'baseTime': item['baseTime'],
            'fcstDate': item['fcstDate'],
            'fcstTime': item['fcstTime'],
            'nx': item['nx'],
            'ny': item['ny']
        }

    mod_result.append({
        'baseDate': base_data['baseDate'],
        'baseTime': base_data['baseTime'],
        'category': category_values,
        'fcstDate': base_data['fcstDate'],
        'fcstTime': base_data['fcstTime'],
        'nx': base_data['nx'],
        'ny': base_data['ny']
    })

    lgt = mod_result[0]["category"]["LGT"]
    pty = mod_result[0]["category"]["PTY"]
    rn1 = mod_result[0]["category"]["RN1"]
    sky = mod_result[0]["category"]["SKY"]
    t1h = mod_result[0]["category"]["T1H"]
    wsd = mod_result[0]["category"]["WSD"]

    #   lgt, pty, rn1, sky, t1h, wsd = weather_info()
  
  
    prompt = f"""The assistant greets the user based on the given user info and user health information.
                The assistant should talk to the elderly like a friendly neighbor.
                The assistant uses casual and informal greeting style.
                The assistant should not use difficult words or phrases.
                The assistant speaks only English and is designed to help the elderly who can understand only english.

                The assistant should greet the user based on the given current weather information.
                User information = username : {user_info['username']}
                Weather information parameters :
                    LGT means thunderstroke and the value means 에너지밀도(0.2~100kA/㎢) of thunderstroke
                    PTY means precipitation type and the value means 없음(0), 비(1), 비/눈(2), 눈(3), 빗방울(5), 빗방울눈날림(6), 눈날림(7)
                    RN1 means 1시간 강수량(mm)
                    SKY means sky condition and the value means 맑음(1), 구름조금(2), 구름많음(3), 흐림(4)
                    T1H means temperature(°C)
                    WSD means wind speed(m/s)
                - Current weather information 
                    LGT : "{lgt}, PTY : {pty}, RN1 : {rn1}, SKY : {sky}, T1H : {t1h}, WSD : {wsd}
                    
                    The assistant should greet the user based on the given current weather information.
                    The assistant should use short and concise greeting with weather information
                    The assistant should ask the user's feeling in the greeting message.
                        ex) 안녕하세요. 이보람님. 오늘은 구름이 많이꼈네요. 날씨 때문에 우울하지는 않으세요?"""
                        
    # 사용자 입력을 맥락에 추가
    userhiddengreeting = [{"role": "user", "content": "안녕!" }]


    greetingresponse = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role":"system", "content": prompt}] + userhiddengreeting,
        max_tokens=1024,
        temperature=0.5,
        stop=["\n"]
    )

    response_text = greetingresponse.choices[0].message.content
    greeting_time = datetime.now(timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S')
    conv_model = greetingresponse.model
    conversation_start = "make_weather_greeting"
    context.append({"role": "assistant", "content": response_text})
    session.context_counter += 1  # Increment context_counter
    context_counter = session.context_counter
    save_context_to_db(session, context_counter, user_info, "assistant", greeting_time, response_text, conversation_start, conv_model)


    return response_text  



# %%
# summarization
def get_previous_conversation(phone_id): # get previous conversation to summarize the conversation and then derive next greeting message from the summarization
    try:
        
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor()

        # 방금 대화 내용 요약 쿼리
        query = """SELECT session_id, conversation_start, role, content
        FROM context
        WHERE phone_id = %s AND session_id = (
            SELECT session_id
            FROM context WHERE phone_id = %s
            ORDER BY created_at DESC
            LIMIT 1
        ) ORDER BY created_at ASC;"""
        values = (phone_id, phone_id)

        
        cursor.execute(query, values)
        previous_conversation = cursor.fetchall()

        print("Previous conversation retrieved from the database.")

    except pymysql.Error as error:
        print(f"Error while connecting to MySQL: {error}")

    finally:
        cursor.close()
        connection.close()
    
    return previous_conversation

def make_summ_nextgreeting_from_chat(session, user_info): # make summarization and nextgreeting from chat using chatgpt(gpt-4o)
    # global username
    phone_id = session.phone_id
    previous_conversation = get_previous_conversation(phone_id)
    conversation_start = previous_conversation[0][1]
    current_time_summ = datetime.now(timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S')
    conv_session_id = previous_conversation[0][0]


    # prompt for summarization of previous conversation
    prompt_sum = """you have to summarize the previous conversation.
                    The summarization is consisted of only english.
                    The summarization should be short and concise."""


    request_summarization = f"""Summarize previous conversation given below
                            
                            user and assistant previous conversation:
                            {previous_conversation}"""

    
    context_sum = [{"role": "user", "content": request_summarization}]
    

    # ChatGPT-4o API 호출
    response_sum = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role":"system", "content": prompt_sum}] + context_sum,
        max_tokens=1024,
        temperature=0.5,
        stop=["\n"]
    )
    
    
    # prompt for next greeting message
    prompt_greeting = """you have to give a first greeting question to start a new conversation, which is based on the summarization of the previous conversation.
                The first greeting question is to talk to the elderly like a friendly neighbor.
                The first greeting question uses casual and informal conversation style. 
                The first greeting question should not use difficult words or phrases.
                The first greeting question is consisted of only english.
                
                The first greeting question should be "short".
                Remember to keep the conversation friendly and casual like a chat buddy.
                """
    
    request_greeting = f"""Give me a appropriate greeting question to start next conversation.
                        The greeting question should be related to summarization that you made.
                        Additionally, the answer should start with a greeting and my nmae.
                        The first greeting question is consisted of only english.
                            ex) Hello! Boram Lee! [greeting question]
                            
                        My name : {user_info['username']}
                        
                        Summarization of previous conversation:
                        {response_sum.choices[0].message.content}"""



    context_greeting = [{"role": "user", "content": request_greeting}]
    
    response_greeting = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role":"system", "content": prompt_greeting}] + context_greeting,
        max_tokens=1024,
        temperature=0.5,
        stop=["\n"]
    )
    
    # in case that user does not answer the medication alarm (but app access is processed)
    # if len(previous_conversation) == 1 and conversation_start == 'med_regular_greeting' and previous_conversation[0][2] == "assistant":
    #     response_sum_text = 0
    #     response_greeting_text = 0
    #     summ_model = 0
    # else:
    
    response_sum_text = response_sum.choices[0].message.content
    response_greeting_text = response_greeting.choices[0].message.content
    
    summ_model = response_sum.model
    


    return response_sum_text, response_greeting_text, current_time_summ, conv_session_id, summ_model, conversation_start



# insert summarization information into summarization table
def save_summarization_to_db(session, user_info):
    phone_id = session.phone_id
    username = user_info["username"]
    response_sum_text, response_greeting_text, current_time_summ, conv_session_id, summ_model, conversation_start = make_summ_nextgreeting_from_chat(session, user_info)
    #mix
    try:
        
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor()

        query = "INSERT INTO summarization (session_id, summ_created_at, phone_id, username, conversation_start, summary_model, summary, next_first_question) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
        values = (conv_session_id, current_time_summ, phone_id, username, conversation_start, summ_model, response_sum_text, response_greeting_text)

        
        cursor.execute(query, values)
        connection.commit()

        print("Summarization and next greeting saved to the database.")

    except pymysql.Error as error:
        print(f"Error while connecting to MySQL: {error}")

    finally:
        cursor.close()
        connection.close()



# %%

# function to decide which greeting message for casual alarm should be derived according to situation and timearea
def casual_greeting(session, user_info, casual_situation):
    # global context
    context = session.context
    
    if casual_situation == 'morning':
        response = make_weather_greeting(session, user_info)
    elif casual_situation == 'summarization':
        response = get_greeting_from_summarization(session, user_info)
    elif casual_situation == 'evening':
        _, response_greeting, *_ = make_summ_nextgreeting_from_chat(session, user_info) #mix
        response = response_greeting
        greeting_time = datetime.now(timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S') 
        conv_model = "script and summarization"
        conversation_start = "casual_greeting"
        context.append({"role": "assistant", "content": response})
        session.context_counter += 1  # Increment context_counter
        context_counter = session.context_counter
        save_context_to_db(session, context_counter, user_info, "assistant", greeting_time, response, conversation_start, conv_model)
    return response

# get medication reminding greeting for mecication reminding alarm 
def med_reminding_greeting(session, user_info):
    # global context
    # global username
    # global current_timearea
    context = session.context
    username = user_info['username']
    current_timearea = session.current_timearea
    
    
    if current_timearea == '점심':
        prev_current_timearea = '아침'
    elif current_timearea == '저녁':
        prev_current_timearea = '점심'
    elif current_timearea in ['아침', '새벽']:
        prev_current_timearea = '어제'

    response = "Dear {username}, I was worried because you didn’t answer my call {prev_current_timearea}. Did you take your medication {prev_current_timearea}?"
    greeting_time = datetime.now(timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S') 
    conv_model = "scripted"
    conversation_start = "med_reminding_greeting"
    
    context.append({"role": "assistant", "content": response})
    session.context_counter += 1
    context_counter = session.context_counter
    save_context_to_db(session, context_counter, user_info, "assistant", greeting_time, response, conversation_start, conv_model)
    
    return response



# get casual greeting
def get_casual_greeting(session, user_info):
    
    current_timearea = session.current_timearea

    if current_timearea == '아침':
        response = casual_greeting(session, user_info, 'morning')
    elif current_timearea == '점심':
        response = casual_greeting(session, user_info, 'summarization')
    elif current_timearea == '저녁':
        response = casual_greeting(session, user_info, 'evening')
    else:
        response = casual_greeting(session, user_info, 'summarization')
    return response


# function to decide which greeting message should be derived according to situation and timearea
def get_greeting_response(session, user_info):

    session_close_key = session.close_key
    prev_summary = user_info["prev_summary"]
    prev_conversation_start = user_info["prev_conversation_start"]
    
    if session_close_key == 'casual':
        if prev_summary == 0 :
            if prev_conversation_start == 'med_regular_greeting':
                resposne = med_reminding_greeting(session, user_info)
            else:
                response = get_casual_greeting(session, user_info)
        else:
                response = get_casual_greeting(session, user_info)
    elif session_close_key in ['health_med_alarm', 'health_inj_alarm']:
        response = med_regular_greeting(session, user_info)
    else:
        response = get_casual_greeting(session, user_info)
    return response
    


# %%


# chat with gpt and save context in memory and in database
def chat_with_gpt(session, user_info, user_input, created_time):

    context = session.context
    context_string = session.context_string
    username = user_info['username']
    usersex = user_info['usersex']
    userage = user_info['userage']
    user_diseases = json.loads(user_info['disease']) if user_info['disease'] else []
    medication_alarm = user_info['health_med_alarm']
    injection_alarm = user_info['health_inj_alarm']
    user_healthissues = user_info['healthissue']


    temp_currenttime = datetime.now(timezone('Asia/Seoul')).time()
    

    # Iterate over each dictionary in the context list
    for i, entry in enumerate(context):
        # Format the string as 'role:content\n' (add '\n' if not the last item)
        if i < len(context) - 1:
            formatted_string = f"{entry['role']}:{entry['content']}\n"
        else:
            formatted_string = f"{entry['role']}:{entry['content']}"
        
        # Append the formatted string to the context_string list
        context_string.append(formatted_string)
    
    korea_medication_alarm = adjust_times(medication_alarm, 9)
    korea_injection_alarm = adjust_times(injection_alarm, 9)
    
    prompt = f"""The following is a friendly conversation between a human and an assistant.
                The assistant should talk to the elderly like a friendly neighbor.
                The assistant uses casual and informal conversation style.
                The assistant cares about the health, daily life, and family of the elderly.    
                The assistant is talking to the elderly who wants to have friendly conversation. 
                The assistant is to help the elderly relieve their depression or gloomy mood by having a conversation. 
                The assistant should not use difficult words or phrases, and should be patient and understanding.
                The assistant should give a question to the elderly to keep the conversation going.
                The assistant speaks only English and is designed to help the elderly who can understand only english.
                
                The assistant must respond "shortly" with no more than three sentences each time.
                Remember to keep the conversation friendly and casual like a chat buddy.
                Below is friendly and casual statement for the assistant to use.
                ex) Oh, I heard your knee’s been bothering you and you haven’t been able to exercise. 
                    My grandma went through the same thing. But don’t give up! Try doing some light exercises like swimming or stretching. If you keep at it, you’ll get stronger and it won’t hurt as much. You can do it, seriously. Go for it!

                The assistant should understand and internalize the given user information and engage in conversation with the user based on this information.
                User information = username : {username}, user gdnder : {usersex}, user age : {userage}
                
                - User health information that the assistant can reference during the conversation:
                The user's illnesses or diseases : {user_diseases}
                The user's medication the user needs to take and the times they need to take it : {korea_medication_alarm}
                The user's injection medication the user needs to take and the times they need to take it: {korea_injection_alarm}
                Current time : {temp_currenttime}
                The user's other healthissues : {user_healthissues}
                
                
                Previous conversation:\n{context_string}\n\nuser: {user_input}\nassistant:"""

    context.append({"role": "user", "content": user_input})
    session.context_counter += 1  # Increment context_counter
    context_counter = session.context_counter
    save_context_to_db(session, context_counter, user_info, "user", created_time, user_input)
    
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role":"system", "content": prompt}] + context,
        max_tokens=1024,
        temperature=0.5,
        stop=["\n"]
    )
    
    response_text = response.choices[0].message.content
    response_time = datetime.now(timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S') 
    conv_model = response.model
    
    context.append({"role": "assistant", "content": response_text})
    session.context_counter += 1  # Increment context_counter
    context_counter = session.context_counter
    
    conversation_start = None
    save_context_to_db(session, context_counter, user_info, "assistant", response_time, response_text, conversation_start, conv_model)
 
    return response_text



# %%

# play chatgpt resposne with tts (return audiofile)
def play_chatgpt_response_with_tts(ai_response, file_path):
    # Generate speech from the GPT response using TTS

    with openai_client.audio.speech.with_streaming_response.create(
        model="tts-1-hd",
        voice="nova",
        input=ai_response,
        response_format="wav",
        speed=0.92
    ) as response:
        response.stream_to_file(file_path)
    
    return response

# %%
# save audio file(.wav) 
def save_wav(buffer, file_path):
    with wave.open(file_path, "wb") as wav_file: # "uploaded.wav"
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(buffer)

# rename wav file to distinquish conversation order using userevenno
def rename_wav_file(session):
    session_id = session.session_id
    userevenno = session.context_counter-1
    
    humancvs_file_path = f"./useraudiofile/{session_id}_.wav"
    
    new_file_path = f"./useraudiofile/{session_id}_{userevenno:04d}.wav"
    
    try:
        os.rename(humancvs_file_path, new_file_path)
        print(f"File renamed from {humancvs_file_path} to {new_file_path}")
    except FileNotFoundError:
        print(f"File {humancvs_file_path} not found.")
    except Exception as e:
        print(f"Error renaming file: {e}")
    
    return new_file_path

# %%
# update database user information table using username when accessing app at the first time
def phoneid_db_search_update(phone_id, name):
    
    try:
      connection = pymysql.connect(**db_config)
      cursor = connection.cursor()
      
      # 트랜잭션 시작
      connection.begin()
      
      usernameselectquery = """SELECT username FROM user WHERE username = %s;"""
      unsvalues = (name)
      cursor.execute(usernameselectquery,unsvalues)
      data = cursor.fetchall()
      
      if not data:
          return False
      else:
        
        # update user table
        userupdatequery = """UPDATE user 
                                SET phone_id = %s WHERE username = %s;"""
        uuvalues = (phone_id, name)
        cursor.execute(userupdatequery, uuvalues)
        print("Update phone_id to user table.")
        
        
        # update alarm table
        alarmupdatequery = """UPDATE alarm
                                SET phone_id = %s WHERE username = %s;"""
        auvalues = (phone_id, name)
        cursor.execute(alarmupdatequery, auvalues)
        print("Update phone_id to alarm table.")
        
        
        # update healthinfo table
        healthinfoupdatequery = """UPDATE healthinfo
                                    SET phone_id = %s WHERE username = %s;"""
        huvalues = (phone_id, name)
        cursor.execute(healthinfoupdatequery, huvalues)
        print("Update phone_id to healthinfo table.")
        
        #update context table
        contextupdatequery = """UPDATE context
                                SET phone_id = %s WHERE username = %s;"""
        cxvalues = (phone_id, name)
        cursor.execute(contextupdatequery, cxvalues)
        print("Update phone_id to context table.")
        
        # update summarization table
        summupdatequery = """UPDATE summarization
                        SET phone_id = %s WHERE username = %s;"""
        suvalues = (phone_id, name)
        cursor.execute(summupdatequery, suvalues)
        print("Update phone_id to summarization table.")
      
      connection.commit()
      return True

      
    except pymysql.Error as error:
      print(f"Error while connecting to MySQL: {error}")
      connection.rollback()
      return False

    finally:
      cursor.close()
      connection.close()
      

# to display previous conversation history on app whenever accessing the app
def preconv_history_json(phone_id):
    
    try:
      connection = pymysql.connect(**db_config)
      cursor = connection.cursor()
      
      
      recallquery = """SELECT created_at, role, content 
                        FROM (
                            SELECT created_at, role, content 
                            FROM context 
                            WHERE phone_id = %s 
                            ORDER BY created_at DESC 
                            LIMIT 20
                        ) AS subquery 
                        ORDER BY created_at ASC;"""
      values = (phone_id)
      cursor.execute(recallquery,values)
      data = cursor.fetchall()
      
      if not data or (len(data) == 1 and data[0][1] == "initialization"):
          return False
      
    except pymysql.Error as error:
      print(f"Error while connecting to MySQL: {error}")
      connection.rollback()
      return False

    finally:
      cursor.close()
      connection.close()

    messages = []
    for entry in data:
        date_str = entry[0].strftime("%Y년 %m월 %d일")
        time_str = entry[0].strftime("%p %I:%M").replace("AM", "오전").replace("PM", "오후")
        message = {
            "date": date_str,
            "time": time_str,
            "speaker": entry[1],
            "contents": entry[2].strip()
        }
        messages.append(message)

    json_result = {"messages": messages}
    # print(json.dumps(json_result, ensure_ascii=False, indent=4))
    print(json_result)
    
    return json_result

# %%

## by. HJ##
def read_version_from_file(file_path):
    try:
        with open(file_path, 'r') as file:
            version = file.readline().strip()
            return version
    except FileNotFoundError:
        return None
    except IOError:
        return None
##############################

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.user_info: Dict[str, UserInfo] = {}
        self.session: Dict[str, UserSession] = {}

            
    async def connect(self, websocket: WebSocket, phone_id: str):
        await websocket.accept()
        self.active_connections[phone_id] = websocket
        self.updateUserInfo(phone_id)
    
    
    def getUserInfo(self, phone_id: str):
        return self.user_info[phone_id]
    
    
    def getUserInfo2Json(self, phone_id: str):
        info = self.user_info[phone_id]
        return { "phone_id": phone_id, 
                "username": info['username'], 
                "usersex": info['usersex'], 
                "userage": info['userage'],
                "user_diseases": json.loads(info['disease']) if info['disease'] else [],
                "casual_alarm": info['casualalarm'][0]['casual'] if info['casualalarm'] else [],
                "medication_alarm": info['health_med_alarm'] if info['health_med_alarm'] else [],
                "injection_alarm": info['health_inj_alarm'] if info['health_inj_alarm'] else [],
                "user_healthissues": info['healthissue'] if info['healthissue'] else []}
    
    
    def getSession(self, phone_id: str):
        return self.session[phone_id]
    
    
    def updateUserInfo(self, phone_id: str):
        user_info_obj = UserInfo(db_config)
        self.user_info[phone_id] = user_info_obj.get_user_info(phone_id)
        if self.user_info[phone_id] is not None:
            self.session[phone_id] = UserSession(phone_id, self.user_info[phone_id])
    
        
    def disconnect(self, phone_id: str):
        if phone_id in self.active_connections:
            del self.active_connections[phone_id]
            
        if phone_id in self.user_info:
            del self.user_info[phone_id]
            
        if phone_id in self.session:
            del self.session[phone_id]
            
    
    async def send_message(self, message: str, phone_id: str):
        websocket = self.active_connections.get(phone_id)
        if websocket:
            await websocket.send_text(message)
    
    async def broadcast(self, message: str):
        for websocket in self.active_connections.values():
            await websocket.send_text(message)
            

manager = ConnectionManager()
app = FastAPI()


@app.websocket("/ws/{phone_id}")
async def websocket_endpoint(websocket: WebSocket, phone_id: str):
    await manager.connect(websocket, phone_id)
    print(f"client connected: {websocket.client}")
 
    #user_info = None
    #session = None
    
    context = []
    context_string = []
    context_counter = 0

    # try:
    while True:

        try:
            data = await websocket.receive_text()
            print(f"Received text data: {data}")
            command = data.split("#")
            
            if len(command) < 2:
                continue
            
            # automatically update app
            if "version" in command[0]:
                version_file_path = "/var/www/html/downloads/seniorcare/version.txt"
                version = read_version_from_file(version_file_path)
                
                if version:
                    await manager.send_message(f"version#{version}", phone_id)
                else:
                    await manager.send_message("version#ERROR", phone_id)

            # when getting search command from app, search user information whenevr accessing app
            elif "search" in command[0]:
                uid = command[1]
                if manager.getUserInfo(uid) is None: # if userinfo is none send "sesarch#error_no_user" to app
                    await manager.send_message("search#error_no_user", uid)
                    continue

                # send userinfo as json format with "search" command to app
                await manager.send_message(f"search#{json.dumps(manager.getUserInfo2Json(uid),default=str, ensure_ascii = False)}", uid)
                print(f"search#{json.dumps(manager.getUserInfo2Json(uid),default=str, ensure_ascii = False)}")

            # when getting register command from app, search database and update database according to the existance of user information
            elif "register" in command[0]:
                uid = command[1]
                name = command[2]
                if phoneid_db_search_update(uid, name): # db에 이름이 있어서 phone_id 업데이트 성공한 경우
                        await manager.send_message("register#OK", uid)
                        manager.updateUserInfo(uid)
                        
                else:
                        await manager.send_message("register#ERROR", uid)

            # when getting prev_cvs command from app, send previous conversation to app
            elif "prev_cvs" in command[0]:
                uid = command[1]
                await manager.send_message(f"prev_cvs#{json.dumps(preconv_history_json(uid), default = str, ensure_ascii=False)}", uid)
                
            # when getting welcome_tts command from app, send greeting_text derived from get_greeting_response function and audio file
            elif "welcome_tts" in command[0]:
                uid = command[1]
                greeting_text = get_greeting_response(manager.getSession(uid), manager.getUserInfo(uid))

                greeting_file_path = f"./{manager.getSession(uid).session_id}_greeting.wav"
                play_chatgpt_response_with_tts(greeting_text, greeting_file_path)

                with open(greeting_file_path, "rb") as audio_file:
                    greeting_wav = audio_file.read()
                    greeting_wav = base64.b64encode(greeting_wav)
                    # print(len(greeting_wav))
                    await manager.send_message(f"welcome_tts#{greeting_wav}", uid)
                os.remove(greeting_file_path)
                await manager.send_message(f"welcome_tts_text#{greeting_text}", uid)
                print("greeting text : ", greeting_text)
          
            # when getting human_cvs command and human answer audiofile from app, save audiofile in server, transform speech to text, send the text to app
            elif "human_cvs" in command[0]:
                uid = command[1]
                audio_data = command[2]
                audio_data = base64.b64decode(audio_data)
                # print(len(audio_data))
                humancvs_file_path = f"./useraudiofile/{manager.getSession(uid).session_id}_.wav" # human audiofile path
                save_wav(audio_data, humancvs_file_path) # save human audiofile

                transcription_text = get_transcript(humancvs_file_path) # stt process
                transcript_time = datetime.now(timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S') # to save time of transcript into database
                await manager.send_message(f"human_cvs_text#{transcription_text}", uid)
                print("transcription_text : ", transcription_text)

                llm_response = chat_with_gpt(manager.getSession(uid), manager.getUserInfo(uid), transcription_text, transcript_time) # derive chatgpt answer
                renamewavfile = rename_wav_file(manager.getSession(uid))
                save_audiodir_to_context(manager.getSession(uid), renamewavfile)
                
                response_file_path = f"./{manager.getSession(uid).session_id}_response.wav"
                play_chatgpt_response_with_tts(llm_response, response_file_path)

                with open(response_file_path, "rb") as audio_file:
                    answer_wav = audio_file.read()
                    answer_wav = base64.b64encode(answer_wav)
                    # print(len(answer_wav))
                    await manager.send_message(f"ai_cvs#{answer_wav}", uid) # send ai answer audiofile to app
                os.remove(response_file_path)
                await manager.send_message(f"ai_cvs_text#{llm_response}", uid) # send ai answer text to app
                print("llm_response : ", llm_response)

        except WebSocketDisconnect:
            print("Websocket disconnected")
            # when websocket disconnect, summarization start
            save_summarization_to_db(manager.getSession(uid), manager.getUserInfo(phone_id) if manager.getUserInfo(phone_id) else None)
            print("Summarization saved to the database.")
            manager.disconnect(uid)
            break
        
        except Exception as e:
            print(f"Error: {e}")
            # when error, summarization start
            save_summarization_to_db(manager.getSession(uid), manager.getUserInfo(phone_id) if manager.getUserInfo(phone_id) else None)
            manager.disconnect(uid)
            break

def run():
    from hypercorn.config import Config
    from hypercorn.asyncio import serve
    import asyncio
    
    config = Config()
    config.bind = ["0.0.0.0:8845"]
    config.websocket_max_message_size = 16 * 1024 * 1024
    asyncio.run(serve(app, config))
    
    #uvicorn.run(app, host='0.0.0.0', port=8845, ws_max_size=16 * 1024 * 1024)
        
        
if __name__ == "__main__":
    run()
    


