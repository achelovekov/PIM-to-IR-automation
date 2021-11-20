from nornir.core.inventory import Host
from nornir.core.task import Task, Result
from nornir import InitNornir
from nornir_utils.plugins.functions import print_result
from nornir_scrapli.tasks import send_command
from nornir_utils.plugins.tasks.files import write_file
import yaml
import json
from jinja2 import Template
import os
import shutil
import argparse

def saveResult(task, result):
    filename = task.host.name
    task.run(task=write_file, filename=filename, content = result[task.host.name].result)

def parseYaml(filename):
    with open(filename, "r") as stream:
        try:
            return yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(exc)

def createInventoryDi(nr):
    di = {}
    for item in nr.inventory.hosts:
        di[item] = []
    return di

def launcher(vnis, nr):
    globalResult = createInventoryDi(nr)
    for vni in vnis:
        print(f"go for {vni}")
        command = f"show nve vni {vni} | json "
        result = nr.run(task=send_command, command=command)
        if result.failed:
            for key, _ in result.failed_hosts.items():
                print(f"cannot connect to {key}")
        for hostname, data in result.items():
            if data[0].result != '':
                print(hostname, data[0].result)
                r = json.loads(data[0].result)
                if ( r['TABLE_nve_vni']['ROW_nve_vni']['mcast'] != 'UnicastBGP' and
                     r['TABLE_nve_vni']['ROW_nve_vni']['mcast'] != 'n/a' ):
                    globalResult[hostname].append(r)
    return globalResult

def generateRenderedData(data):
    template = """
interface nve1
{% for item in data %}
 member vni {{ item["TABLE_nve_vni"]["ROW_nve_vni"]["vni"] }}
   no mcast-group
   ingress-replication protocol bgp
!
{% endfor %}
"""
    j2_inventory_template = Template(template)

    return j2_inventory_template.render(data=data)

def writeDataToFile(filename, data):
    if '/' in filename:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, 'w') as f:
        f.write(data)

def generateConfigs(di, stageNum):
    shutil.rmtree(stageNum, ignore_errors=True)
    for hostname, data in di.items():
        if len(data) > 0:
            writeDataToFile(f"{stageNum}/configs/{hostname}-{stageNum}.cfg", generateRenderedData(data))

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Optional app description')
    parser.add_argument('path', type=str, help='nornir path')
    parser.add_argument('vniFile', type=str, help='A required yaml file with vni list for stage')
    args = parser.parse_args()

    data = parseYaml(args.vniFile)
    nr = InitNornir(config_file=f"{args.path}/config.yaml")
    globalResult = launcher(data['VNI_LIST'], nr)
    generateConfigs(globalResult, data['JOB'], args.path)

