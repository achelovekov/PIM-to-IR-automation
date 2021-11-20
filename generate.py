import csv
from typing import Dict, List, Tuple, Any, Set
from tqdm import tqdm
import os 
import sys
import re
import shutil
from pydantic import BaseModel
 
import argparse
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

    @classmethod
    def readFileLines(cls, filename):
        try:
            with open(filename, encoding = 'utf-8') as f:
                lines = [line.rstrip() for line in f.readlines()]
                return lines 
        except Exception as e:
            print(f"{e}")

class Ansible(BaseModel):
    @classmethod
    def hosts(cls, rooms, stageNum, hostsWithIps, ansiblePath):
        template_perStage = """
[stage_{{ stageNum }}]
{% for item in data -%}
{{ item[0] }}\tansible_host={{ item[1] }}
{% endfor %}
"""
        template_all = """
[{{ room }}]
{% for item in data -%}
{{ item[0] }}\tansible_host={{ item[1] }}
{% endfor %}
""" 
        j2_inventory_template_perStage = Template(template_perStage)
        j2_inventory_template_all = Template(template_all)

        if stageNum != 'all_stages':
            Auxilary.writeDataToFile(f'{ansiblePath}/inventories/__stage_{stageNum}_inventory_new.yml', 
                    j2_inventory_template_perStage.render(stageNum=stageNum, data=hostsWithIps), 
                    'a')
        else:
            for room in rooms:
                data = Ansible.splitHostsByRoom(room, hostsWithIps)
                print(room)
                if len(data) > 0: 
                    Auxilary.writeDataToFile(f'{ansiblePath}/inventories/hosts.yml', 
                            j2_inventory_template_all.render(room=room, data=data), 
                            'a')
        
    @classmethod
    def extraVars(cls, stageNum, data, vrf, ansiblePath):
        template = """
JOB_NAME: "stage_{{ stageNum }}"
VNI_LIST:
    {% for item in data -%}
        - "{{ item }}"
    {% endfor %}

VRF: "{{ vrf }}"
AGW: True

# DO NOT CHANGE
HOSTS: "all"
"""
        j2_inventory_template = Template(template)

        Auxilary.writeDataToFile(f'{ansiblePath}/inventories/stage_{stageNum}_extra_vars.yml', 
            j2_inventory_template.render(data=data, stageNum=stageNum, vrf=vrf), 
            'w')

    @classmethod
    def groupVars(cls, username, password, scpHost, ansiblePath):
        template = """
# Network admin credentials
ansible_user: {{ username }}
ansible_password: {{ password }}

#SCP settings
SCP_USER: "scp_user"
SCP_PASSWORD: "scp_pass"
SCP_HOST: "{{ scpHost }}"

#Ansible vars. DO NOT MODIFY
ansible_connection: "network_cli"
ansible_network_os: "nxos"
"""
        j2_inventory_template = Template(template)

        Auxilary.writeDataToFile(f'{ansiblePath}/inventories/group_vars/all.yml', 
            j2_inventory_template.render(username=username, password=password, scpHost=scpHost), 
            'w')

    @classmethod
    def splitHostsByRoom(cls, room, hostsWithIps) -> List:
        result = []
        for host in hostsWithIps:
            if room == Auxilary.parseGroupData(host[0])['room']:
                result.append(host)                
        return result

class Nornir(BaseModel):
    @classmethod
    def hosts(cls, hostsWithIps, nornirPath):

        template = """
{{ item[0] }}:
    hostname: {{ item[1] }}
    groups:
        - {{ groupData['deviceType'] }}
        - {{ groupData['room'] }}
        - {{ groupData['segment'] }}

"""  
        j2_inventory_template = Template(template)

        Auxilary.writeDataToFile(f'{nornirPath}/inventory/hosts.yaml', '---', 'a')
        for item in hostsWithIps:
            Auxilary.writeDataToFile(f'{nornirPath}/inventory/hosts.yaml', 
                j2_inventory_template.render(item=item, 
                groupData=Auxilary.parseGroupData(item[0])), 'a')

    @classmethod
    def groups(cls, deviceTypes, rooms, segments, nornirPath):
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

        Auxilary.writeDataToFile(f'{nornirPath}/inventory/groups.yaml', 
            j2_inventory_template.render(deviceTypes=deviceTypes, rooms=rooms, segments=segments),
            'w')

    @classmethod
    def defaults(cls, username, password, nornirPath):
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

        Auxilary.writeDataToFile(f'{nornirPath}/inventory/defaults.yaml', 
            j2_inventory_template.render(username=username, password=password),
            'w')

    @classmethod
    def config(cls, nornirPath):
        template = """
---
inventory:
    plugin: SimpleInventory
    options:
        host_file: "{{ nornirPath }}/inventory/hosts.yaml"
        group_file: "{{ nornirPath }}/inventory/groups.yaml"
        defaults_file: "{{ nornirPath }}/inventory/defaults.yaml"
runner:
    plugin: threaded
    options:
        num_workers: 100    
"""
        j2_inventory_template = Template(template)

        Auxilary.writeDataToFile(f'{nornirPath}/config.yaml', 
            j2_inventory_template.render(nornirPath=nornirPath),
            'w')

    @classmethod
    def vnis(cls, stageNum, vnis, nornirPath):
        template = """
---
JOB: "stage_{{ stageNum }}"
VNI_LIST:
    {% for item in vnis -%}
    - "{{ item }}"
    {% endfor -%}
"""
        j2_inventory_template = Template(template)

        Auxilary.writeDataToFile(f'{nornirPath}/vnis_stage_{stageNum}.yaml', 
            j2_inventory_template.render(stageNum=stageNum, vnis=vnis),
            'w')

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
    deviceTypes: Set = set()
    rooms: Set = set()
    segments: Set = set()
    
    def append(self, dBEntry:DBEntry):
        self.root.append(dBEntry)
    
    def __iter__(self):
        return iter(self.root)
    
    def getUniqueRooms(self):
        self.rooms = {dBEntry.room for dBEntry in self}
    
    def getUniqueDeviceTypes(self):
        self.deviceTypes = {dBEntry.deviceType for dBEntry in self}
    
    def getUniqueSegments(self):
        self.segments = {dBEntry.segment for dBEntry in self}

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
        
        self.getUniqueDeviceTypes()
        self.getUniqueSegments()
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
    
    def generatePerStageData(self, stageFile):
        regex = ("(?P<stageNum>(\d+))")
        stageNum = re.search(regex, stageFile).group('stageNum')
        subnets = Auxilary.readFileLines(stageFile)
        vnis = self.getUniqueVnisBySubnets(subnets)
        hostsWithIps = self.getUniqueHostnamesWithIpsBySubnets(subnets)

        return stageNum, vnis, hostsWithIps

    def generateAnsibleDataPerStage(self, vrf, ansiblePath, *stageFiles):
        for stageFile in stageFiles:
            stageNum, vnis, hostsWithIps = self.generatePerStageData(stageFile)

            Ansible.hosts(self.rooms, stageNum, hostsWithIps, ansiblePath)
            Ansible.extraVars(stageNum, vnis, vrf, ansiblePath)
    
    def generateAnsibleDataAllStages(self, allStageData, username, password, scpHost, ansiblePath):
        hostsWithIps = self.getUniqueHostnamesWithIpsBySubnets(allStageData)
        Ansible.hosts(self.rooms, 'all_stages', hostsWithIps, ansiblePath)
        Ansible.groupVars(username, password, scpHost, ansiblePath)

    def generateAnsibleData(self, allStageData, vrf, username, password, scpHost, ansiblePath, *stageFiles):
        shutil.rmtree(f'{ansiblePath}/inventories', ignore_errors=True)

        self.generateAnsibleDataAllStages(allStageData, username, password, scpHost, ansiblePath)
        self.generateAnsibleDataPerStage(vrf, ansiblePath, *stageFiles)

    def generateNornirDataPerStage(self, nornirPath, *stageFiles):
        for stageFile in stageFiles:
            stageNum, vnis, _ = self.generatePerStageData(stageFile)
            Nornir.vnis(stageNum, vnis, nornirPath)

    def generateNornirDataAllStages(self, allStageData, username, password, nornirPath):
        hostsWithIps = self.getUniqueHostnamesWithIpsBySubnets(allStageData)
        
        Nornir.hosts(hostsWithIps, nornirPath)
        Nornir.groups(self.deviceTypes, self.rooms, self.segments, nornirPath)
        Nornir.defaults(username, password, nornirPath)
        Nornir.config(nornirPath)

    def generateNornirData(self, allStageData, username, password, nornirPath, *stageFiles):
        shutil.rmtree(nornirPath, ignore_errors=True)

        self.generateNornirDataAllStages(allStageData, username, password, nornirPath)
        self.generateNornirDataPerStage(nornirPath, *stageFiles)

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Optional app description')
    parser.add_argument('nornirPath', type=str, help='A required nornir results folder path')
    parser.add_argument('ansiblePath', type=str, help='A required ansible results folder path')
    args = parser.parse_args()

    _, vniListdata = Auxilary.csvReader('EXT_All_vni_list_hwdb.20211115-151709.csv')
    _, mgmtData = Auxilary.csvReader('EXT_SKO_All_switches_hwdb.20211115-121829.csv')
    allStagesData = Auxilary.parseStageData()
    filter = ["SKO", "EXT"]

    db = DB().construct(filter, vniListdata, mgmtData, allStagesData)
    db.generateAnsibleData(allStagesData, 
        'eMZ', 
        'cspc',
        '*********',
        '10.1.1.1',
        args.ansiblePath,
        'subnets_stage_1.plain', 
        'subnets_stage_2.plain',
        'subnets_stage_3.plain',
        'subnets_stage_4.plain',
        'subnets_stage_5.plain'
        )
    db.generateNornirData(allStagesData,
        'cspc',
        '*********', 
        args.nornirPath,
        'subnets_stage_1.plain', 
        'subnets_stage_2.plain', 
        'subnets_stage_3.plain', 
        'subnets_stage_4.plain', 
        'subnets_stage_5.plain'
        )

#исправить стейдж для норнира