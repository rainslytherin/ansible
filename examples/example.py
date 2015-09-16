#!/usr/bin/python
# -*- coding: utf-8 -*-

import ansible.runner
import ansible.playbook
import ansible.inventory
from ansible import callbacks
from ansible import utils
import json

instances = ['10.201.43.174:7000','10.201.43.174:7001','10.201.43.174:7002','10.201.43.175:7000','10.201.43.175:7001','10.201.43.175:7002']
# the fastest way to set up the inventory 
# hosts list 

hosts = ["10.201.43.174","10.201.43.175"]

# set up the inventory, if no group is defined then 'all' group is used by default 
example_inventory = ansible.inventory.Inventory(hosts) 


pm = ansible.runner.Runner( 
    module_name = 'yum', 
    module_args = 'pkg=vipshop-redis-3.0.3', 
    become = True,
    timeout = 5, 
    inventory = example_inventory, 
    subset = 'all' # name of the hosts group 
    ) 

out = pm.run() 

print json.dumps(out, sort_keys=True, indent=4, separators=(',', ': ')) 
~                                                                         