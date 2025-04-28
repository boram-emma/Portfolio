
# Medical Form : http://183.108.254.14:8846/


from datetime import datetime
from pytz import timezone
from typing import List
from fastapi import FastAPI, Request, HTTPException, File, UploadFile, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import pymysql
import json
import shutil
import os

db_config = {
    'host': '127.0.0.1',
    'user': 'root',
    'password': '1234567890',
    'database': 'seniorcare'
}

# 데이터베이스에 데이터 삽입 함수
def insert_healthinfo(phone_id, username, userid, usersex, userage, diseases, medication, injection, healthissues, casual_alarm_time):
    
    try :
        
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor()
        
        insert_user_query = """
        INSERT INTO user (phone_id, username, userid, usersex, userage)
        VALUES (%s, %s, %s, %s, %s)"""
        values = (phone_id, username, userid, usersex, userage)
        cursor.execute(insert_user_query, values)
        connection.commit()
        
        insert_healthinfo_query = """
        INSERT INTO healthinfo (phone_id, username, disease, medication, injection, healthissue)
        VALUES (%s, %s, %s, %s, %s, %s)
        """
        values = (phone_id, username, json.dumps(diseases), json.dumps(medication), json.dumps(injection), healthissues)
        cursor.execute(insert_healthinfo_query, values)
        connection.commit()
        
        
        combined_health = {}

        if medication is not None or not "없음":
            for med in medication:
                combined_health.update(med)
    
                
        if injection is not None or not "없음":
            for inj in injection:
                combined_health.update(inj)

        if combined_health == {}:
            combined_health == None
        
        med_alarm_time = [{"health": combined_health}] #
        casual_alarm_time = [{"casual": casual_alarm_time}]

        insert_alarm_query = """
        INSERT INTO alarm (phone_id, username, casual_alarm_time, med_alarm_time)
        VALUES (%s, %s, %s, %s)
        """
        values = (phone_id, username, json.dumps(casual_alarm_time),json.dumps(med_alarm_time, default=str, ensure_ascii = False))
        
        cursor.execute(insert_alarm_query, values)
        connection.commit()
        
        current_insert_time = datetime.now(timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S')
        initial_greeting = "안녕하세요. 제 이름은 엘프에요. 만나서 반가워요." # "Hello, my name is elf. Nice to meet you!"
        
        conversation_start = "casual_greeting"
        insert_context_query = """INSERT INTO context
        (session_id, unique_number, created_at, phone_id, username, conversation_start, model, role, content)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"""
        values = (f"{datetime.now(timezone('Asia/Seoul')).strftime('%Y%m%d%H%M%S')}_{phone_id}", 0, current_insert_time, phone_id, username, conversation_start, "scripted", "initialization", "안녕!")
        
        cursor.execute(insert_context_query,values)
        connection.commit()
        
        insert_summarization_query = """ 
        INSERT INTO summarization (session_id, summ_created_at, phone_id, username, conversation_start, summary_model, summary, next_first_question)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""
        values = (f"{datetime.now(timezone('Asia/Seoul')).strftime('%Y%m%d%H%M%S')}_{phone_id}", current_insert_time, phone_id, username, 'casual_greeting', 'sum_initialization', 'sum_initialization', initial_greeting)
        
        cursor.execute(insert_summarization_query, values)
        connection.commit()
        
    

    except pymysql.Error as error:
        print(f"Error while connecting to MySQL: {error}")
        raise HTTPException(status_code=500, detail=str(error))

    finally:
        cursor.close()
        connection.close()

app = FastAPI()

class Time(BaseModel):
    name: str
    time: List[str]

class Medication(Time):
    pass

class Injection(Time):
    pass

class FormData(BaseModel):
    userName: str
    userId: str
    userSex: str
    userAge: str
    diseases: List[List[str]]
    casualAlarm: List[List[str]]
    healthIssues: str
    medication: List[Medication]
    injection: List[Injection]

templates = Jinja2Templates(directory='./templates')
upload_dir = "/var/www/html/downloads/seniorcare/"

@app.post("/upload_func")
async def upload_file(file: UploadFile = File(...), version: str = Form(...)):
    os.makedirs(upload_dir, exist_ok=True)
    
    file_path = os.path.join(upload_dir, file.filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    
    version_file_path = os.path.join(upload_dir, "version.txt")
    with open(version_file_path, "w") as version_file:
        version_file.write(version)
        
    return {"filename": file.filename, "version":version}

@app.get("/upload", response_class=HTMLResponse)
async def upload_form(request: Request):
    return templates.TemplateResponse("upload.html", {"request": request})

@app.get("/", response_class=HTMLResponse)
async def read_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/adduser")
async def addUser(data: FormData):
    try:
        trans_data = {
            "userName": data.userName,
            "userId": data.userId,
            "userSex": data.userSex,
            "userAge": int(data.userAge),
            "diseases": [disease for sublist in data.diseases for disease in sublist if disease],
            "casualAlarm": [alarm for sublist in data.casualAlarm for alarm in sublist if alarm],
            "healthIssues": data.healthIssues if data.healthIssues else None,
            "medication": [{med.name: [time for time in med.time if time]} for med in data.medication],
            "injection": [{inj.name: [time for time in inj.time if time]} for inj in data.injection]
        }

        if not trans_data["diseases"]:
            trans_data["diseases"] = None
        if not trans_data["casualAlarm"]:
            trans_data["casualAlarm"] = None
        if not trans_data["medication"]:
            trans_data["medication"] = None
        if not trans_data["injection"]:
            trans_data["injection"] = None

        print(trans_data)
        
        phone_id = f"{trans_data['userName']}_{datetime.now(timezone('Asia/Seoul')).strftime('%Y%m%d%H%M%S')}"
        insert_healthinfo(phone_id, trans_data['userName'], trans_data['userId'], trans_data['userSex'], trans_data['userAge'], trans_data['diseases'], trans_data['medication'], trans_data['injection'], trans_data['healthIssues'], trans_data['casualAlarm'])

        # return {"message": "Data received successfully", "data": trans_data}
    except HTTPException as e:
        raise e
        return e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        return HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8846)