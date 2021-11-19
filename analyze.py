import csv
from typing import Dict, List, Tuple, Any, Set
from tqdm import tqdm
import os 
import sys
import re
import shutil
from pydantic import BaseModel
 
from jinja2 import Template

class Auxilary(BaseModel):
    @classmethod
    def csvReader(cls, filename) -> Tuple[str, str]:
        with open(filename, mode='r') as csv_file:
            data = []
            csv_reader = csv.DictReader(csv_file, delimiter=';')
            line_count = 0
            for row in csv_reader:
                if line_count == 0:
                    headers = (f'Column names are {", ".join(row)}')
                    line_count += 1
                data.append(row)
                line_count += 1
        return (headers, data)

    @classmethod
    def parseStageData(cls) -> List:
        referenceFiles = [f for f in os.listdir('.') if os.path.isfile(f) and 'subnets_stage' in f]
        result = []

        for referenceFile in referenceFiles:
            try:
                with open(referenceFile, encoding = 'utf-8') as f:
                    lines = [line.rstrip() for line in f.readlines()]
                result += lines

            except Exception as e:
                print(f"{e}")
                sys.exit()
        return result

    @classmethod
    def filterCheck(cls, filter, substring) -> bool:
        indicator = True
        for filterItem in filter:
            if filterItem in substring:
                indicator = indicator and True
            else:
                indicator = indicator and False
        return indicator

    @classmethod
    def parseGroupData(cls, hostname) -> Dict:
        regex = (".*(?P<deviceType>(AC|AG|CR|RS|SS|BL|BG|F2FM|ML|C2|SC|BGW|SW))-(?P<room>[A-Z0-9]+).*(?P<segment>(INT|EXT|DMZ|GWN|LAB))")

        result = {}

        try:
            result['deviceType'] = re.search(regex, hostname).group('deviceType')
            result['room'] = re.search(regex, hostname).group('room')
            result['segment'] = re.search(regex, hostname).group('segment')
        except Exception as e:
            print(f'{e}')

        return result

    @classmethod
    def writeDataToFile(cls, filename, data, mode):
        if '/' in filename: 
            os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, mode) as f:
            f.write(data)

class Ansible(BaseModel):
    @classmethod
    def hosts(cls, room, data):
        if len(data) == 0: 
            return ''
        template = """
[{{ room }}]
{% for item in data -%}
{{ item[0] }}\tansible_host={{ item[1] }}
{% endfor %}
"""
        j2_inventory_template = Template(template)

        return j2_inventory_template.render(room=room, data=data)

    @classmethod
    def extraVars(cls, stage, data, vrf):
        template = """
JOB_NAME: "stage_{{ stage }}"
VNI_LIST:
    {% for item in data -%}
        - "{{ item }}"
    {% endfor %}

VRF: {{ vrf }}
AGW: True

# DO NOT CHANGE
HOSTS: "all"
"""
        j2_inventory_template = Template(template)

        return j2_inventory_template.render(data=data, stage=stage, vrf=vrf)

    @classmethod
    def splitHostsByRoom(cls, room, hostsWithIps) -> List:
        result = []
        for host in hostsWithIps:
            if room == Auxilary.parseGroupData(host[0])['room']:
                result.append(host)                
        return result

class DBEntry(BaseModel):
    hostname: str
    vni: str
    subnet: str
    deviceType: str
    room: str
    segment: str  
    ip: str

    def __repr__(self) -> str:
        return super().__repr__()
    
    def __hash__(self) -> int:
        return hash(self.json())
    
    def __eq__(self, other: Any) -> bool:
        return super().__eq__(other)

class DB(BaseModel):
    root: List[DBEntry] = []
    nonManagedHosts: Set = set()
    rooms: Set = set()

    def append(self, dBEntry:DBEntry):
        self.root.append(dBEntry)
    
    def __iter__(self):
        return iter(self.root)
    
    def getUniqueRooms(self):
        self.rooms = {dBEntry.room for dBEntry in self}

    def construct(self, filter, hwdbRawDataVniList, hwdbRawDataMgmt, allStagesSubnets):
        def getMgmtIpByHostname(hwdbRawDataMgmt, hostname) -> str:
            for item in hwdbRawDataMgmt:
                if item['hostname'] == hostname:
                    return item['ip'] if item['ip'] != '' else 'undefined'
            self.nonManagedHosts.add(hostname)
            return 'undefined'

        for row in hwdbRawDataVniList:
            for subnet in allStagesSubnets:
                if ( subnet in row['vlan_name'] and 
                    'ingress' not in row['vni_bum'] and 
                    (
                        filter and Auxilary.filterCheck(filter, row['hostname']) or 
                        not filter
                    )):
                        groupData = Auxilary.parseGroupData(row['hostname'])
                        self.append(DBEntry(
                                hostname=row['hostname'],
                                vni=row['vni'],
                                subnet=subnet,
                                deviceType=groupData['deviceType'], 
                                segment=groupData['segment'],
                                room=groupData['room'],
                                ip=getMgmtIpByHostname(hwdbRawDataMgmt, row['hostname']),
                         ))
        self.getUniqueRooms()
        return self

    def getUniqueHostnames(self):
        return {dBEntry.hostname for dBEntry in self}
    
    def getUniqueVnis(self):
        return {dBEntry.vni for dBEntry in self}

    def getUniqueVnisBySubnets(self, subnets):
        result = set()
        for subnet in subnets:
            for item in self:
                if subnet in item.subnet:
                    result.add(item.vni)

        return result        

    def getUniqueHostnamesWithIpsBySubnets(self, subnets) -> Set[Tuple[str, str]]:
        result = set()
        for subnet in subnets:
            for item in self:
                if ( subnet in item.subnet and
                     item.hostname not in self.nonManagedHosts ): 
                        result.add((item.hostname, item.ip)) 
        return result                 
    
    def generateAnsibleDataPerStage(self, vrf, *stageFiles):
        shutil.rmtree('ansible', ignore_errors=True)
        regex = ("(?P<stageNum>(\d+))")
        for stageFile in stageFiles:
            stageNum = re.search(regex, stageFile).group('stageNum')
            subnets = readFileLines(stageFile)
            vnis = self.getUniqueVnisBySubnets(subnets)
            hostsWithIps = self.getUniqueHostnamesWithIpsBySubnets(subnets)

            for room in self.rooms:
                Auxilary.writeDataToFile(f'ansible/inventories/stage_{stageNum}_inventory_new.yml', 
                                Ansible.hosts(room, Ansible.splitHostsByRoom(room, hostsWithIps)), 
                                'a')
            Auxilary.writeDataToFile(f'ansible/inventories/stage_{stageNum}_extra_vars.yml',
                                Ansible.extraVars(stageNum, vnis, vrf),
                                'w')


def getHostDataFromDB(hostname, db):
    for item in db:
        if item['hostname'] == hostname:
            return item
    return None

def generateNornirInventoryHosts(inventory, db):
    temp_db = [ getHostDataFromDB(host, db) for host in inventory ]

    template = """
---
{% for item in temp_db -%}
{{ item['hostname'] }}:
    hostname: {{ item['ip'] }}
    groups:
        - {{ item['deviceType'] }}
        - {{ item['room'] }}
        - {{ item['segment'] }}

{% endfor -%}
"""  

    j2_inventory_template = Template(template)

    return j2_inventory_template.render(inventory=inventory, temp_db=temp_db)

def generateNornirInventoryGroups(deviceTypes, rooms, segments):
    template = """
---
{% for item in deviceTypes -%}
{{ item }}: 
  data: None

{% endfor -%}

{% for item in rooms -%}
{{ item }}: 
  data: None

{% endfor -%}

{% for item in segments -%}
{{ item }}: 
  data: None

{% endfor -%}
"""  

    j2_inventory_template = Template(template)

    return j2_inventory_template.render(deviceTypes=deviceTypes, rooms=rooms, segments=segments)

def generateNornirDefauls(username, password):
    template = """
---
connection_options:
    scrapli:
        platform: cisco_nxos
        port: 22
        extras:
            ssh_config_file: True
            auth_strict_key: False
port: 22
username: {{ username }}
password: {{ password }}
platform: nxos
"""
    j2_inventory_template = Template(template)

    return j2_inventory_template.render(username=username, password=password)

def generateNornirVniList(vnis):
    template = """
---
VNI_LIST:
    {% for item in vnis -%}
    - "{{ item }}"
    {% endfor -%}
"""
    j2_inventory_template = Template(template)

    return j2_inventory_template.render(vnis=vnis)

def generateNornirConfig():
    template = """
---
inventory:
    plugin: SimpleInventory
    options:
        host_file: "inventory/hosts.yaml"
        group_file: "inventory/groups.yaml"
        defaults_file: "inventory/defaults.yaml"
runner:
    plugin: threaded
    options:
        num_workers: 100    
"""
    j2_inventory_template = Template(template)

    return j2_inventory_template.render()


def readFileLines(filename):
    try:
        with open(filename, encoding = 'utf-8') as f:
            lines = [line.rstrip() for line in f.readlines()]
            return lines 
    except Exception as e:
        print(f"{e}")

vniListHeaders, vniListdata = Auxilary.csvReader('EXT_All_vni_list_hwdb.20211115-151709.csv')
mgmtHeaders, mgmtData = Auxilary.csvReader('EXT_SKO_All_switches_hwdb.20211115-121829.csv')
allStagesData = Auxilary.parseStageData()

db = DB().construct(["SKO", "EXT"], vniListdata, mgmtData, allStagesData)
db.generateAnsibleDataPerStage('eMZ', 'subnets_stage_1.plain', 'subnets_stage_2.plain', 'subnets_stage_3.plain', 'subnets_stage_4.plain', 'subnets_stage_5.plain')
print(db.nonManagedHosts)
""" db, notFound, inventory, vnis, deviceTypes, rooms, segments = constructInventoryDB(filter=["SKO", "EXT"]) """

""" print(*db, sep='\n')
print(f"mgmt ip's not found for:")
print(*notFound, sep='\n')
print("=====")
print(*inventory, sep='\n')
print("=====")
print(*vnis, sep='\n') """

#print(*inventory, sep='\n')
""" 
writeDataToFile('nornir/inventory/hosts.yaml', generateNornirInventoryHosts(inventory, db), 'w')
writeDataToFile('nornir/inventory/groups.yaml', generateNornirInventoryGroups(deviceTypes, rooms, segments), 'w')
writeDataToFile('nornir/inventory/defaults.yaml', generateNornirDefauls('cspc', 'AGP&PsCQ65W3!'), 'w')
writeDataToFile('nornir/vnis.yaml', generateNornirVniList(vnis), 'w')
writeDataToFile('nornir/config.yaml', generateNornirConfig(), 'w')

generateAnsibleInventory(db, rooms)
getHostByVNIs(db, vnis) """

НУЖНО сделать общий hosts для nornir и per-stage vni 