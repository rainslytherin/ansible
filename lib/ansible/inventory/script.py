# -*- coding: utf-8 -*-
# (c) 2012-2014, Michael DeHaan <michael.dehaan@gmail.com>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

#############################################

import os
import subprocess
import ansible.constants as C
from ansible.inventory.host import Host
from ansible.inventory.group import Group
from ansible.module_utils.basic import json_dict_bytes_to_unicode
from ansible import utils
from ansible import errors
import sys


class InventoryScript(object):
    '''
        Host inventory parser for ansible using external inventory scripts.
        当inventory文件为可执行的脚本时的处理逻辑。
    '''


    def __init__(self, filename=C.DEFAULT_HOST_LIST):

        # Support inventory scripts that are not prefixed with some
        # path information but happen to be in the current working
        # directory when '.' is not in PATH.
        self.filename = os.path.abspath(filename) # 获取文件的绝对路径
        cmd = [ self.filename, "--list" ] # 可执行的inventory脚本需要支持--list参数
        try:
            sp = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) # 使用subprocessPopen执行shell命令
        except OSError, e:
            raise errors.AnsibleError("problem running %s (%s)" % (' '.join(cmd), e))
        (stdout, stderr) = sp.communicate() # 获取标准输出

        if sp.returncode != 0: # 如果脚本的返回值（echo $?) 不为0，则报错！
            raise errors.AnsibleError("Inventory script (%s) had an execution error: %s " % (filename,stderr))

        self.data = stdout # 将标准输出存入self.data
        # see comment about _meta below
        self.host_vars_from_top = None
        self.groups = self._parse(stderr) # 解析数据，不过干嘛传入个err。。。蛋疼，ansible的代码也有写的比较烂的地方


    def _parse(self, err):

        all_hosts = {}

        # not passing from_remote because data from CMDB is trusted
        self.raw  = utils.parse_json(self.data) # 还是使用self.data来解析标准输出，需要self.data为json格式数据
        self.raw  = json_dict_bytes_to_unicode(self.raw) # 将self.raw中的kv都转换成unicode格式。

        all       = Group('all') # 设置Group("all")
        groups    = dict(all=all) # 初始化groups字典
        group     = None


        if 'failed' in self.raw: # 如果self.raw中有failed字段，则报错。不过在上面parser_json的时候no_exception是false，不会出现failed情况
            sys.stderr.write(err + "\n")
            raise errors.AnsibleError("failed to parse executable inventory script results: %s" % self.raw)

        for (group_name, data) in self.raw.items():
            # 1.3 以上版本使用--list的时返回结果中会包含_meta这样的key，该key的value中会有一个hostvars变量，该变量包含每个host的主机变量
            # 1.2及以下版本仍然需要使用--host命令为每一个host返回主机变量

            # in Ansible 1.3 and later, a "_meta" subelement may contain
            # a variable "hostvars" which contains a hash for each host
            # if this "hostvars" exists at all then do not call --host for each
            # host.  This is for efficiency and scripts should still return data
            # if called with --host for backwards compat with 1.2 and earlier.


            """
            {
                "databases"   : {
                    "hosts"   : [ "host1.example.com", "host2.example.com" ],
                    "vars"    : {
                        "a"   : true
                    }
                },
                "webservers"  : [ "host2.example.com", "host3.example.com" ],
                "atlanta"     : {
                    "hosts"   : [ "host1.example.com", "host4.example.com", "host5.example.com" ],
                    "vars"    : {
                        "b"   : false
                    },
                    "children": [ "marietta", "5points" ]
                },
                "marietta"    : [ "host6.example.com" ],
                "5points"     : [ "host7.example.com" ]
            }

            {

                # results of inventory script as above go here
                # ...

                "_meta" : {
                   "hostvars" : {
                      "moocow.example.com"     : { "asdf" : 1234 },
                      "llama.example.com"      : { "asdf" : 5678 },
                   }
                }


            {
                "moocow.example.com"     : { "asdf" : 1234 },
                "llama.example.com"      : { "asdf" : 5678 }
            }
            """

            if group_name == '_meta': # 如果key为_meta，则该value为meta数据。
                if 'hostvars' in data:
                    self.host_vars_from_top = data['hostvars'] # 如果meta数据中包含hostvars，则缓存到self.host_vars_from_top中。
                    continue # 跳过之后的处理

            if group_name != all.name: # group_name不是all则创建新的Group对象，并加入groups字典。
                group = groups[group_name] = Group(group_name) # 如果data中出现无group的hostname，则会出现以hostname为名称的组
            else:
                group = all
            host = None

            if not isinstance(data, dict): # 如果data不是字典类型，则表示data为主机列表
                data = {'hosts': data}
            # is not those subkeys, then simplified syntax, host with vars
            elif not any(k in data for k in ('hosts','vars','children')): # any函数表示可迭代对象中任何一个为True，则为True.否则为False
                # 如果data对象是字典类型，但key中不存在hosts、vars、children时，既该行数据为主机变量数据时，group_name为主机名
                data = {'hosts': [group_name], 'vars': data}

            # 上面两步为了统一data的格式，不带变量的data，格式为{'hosts': [host1,host2...]
            # 带变量的data，格式为: {'hosts': [group_name], 'vars': data }

            if 'hosts' in data:
                if not isinstance(data['hosts'], list): # 主机列表必须是list对象，上面的'hosts': [group_name] 也是为校验统一格式
                    raise errors.AnsibleError("You defined a group \"%s\" with bad "
                        "data for the host list:\n %s" % (group_name, data))

                for hostname in data['hosts']: # 遍历主机名
                    if not hostname in all_hosts:
                        all_hosts[hostname] = Host(hostname) # 如果主机对象不存在， 则创建并添加到all_hosts列表中，去重
                    host = all_hosts[hostname] # 将Host对象赋值给host变量
                    group.add_host(host) # 将Host对象添加到该group中

            if 'vars' in data: # 如果是带有vars的data
                if not isinstance(data['vars'], dict):
                    raise errors.AnsibleError("You defined a group \"%s\" with bad "
                        "data for variables:\n %s" % (group_name, data))

                for k, v in data['vars'].iteritems(): # 遍历所有的变量
                    if group.name == all.name: # 如果当前组名为“all",则将变量加到all group中，否则加到当前group中。
                        all.set_variable(k, v)
                    else:
                        group.set_variable(k, v)

        # Separate loop to ensure all groups are defined
        for (group_name, data) in self.raw.items():
            if group_name == '_meta':
                continue
            if isinstance(data, dict) and 'children' in data: # 如果data中包含子组，遍历子组并将子组添加到父组中
                for child_name in data['children']:
                    if child_name in groups:
                        groups[group_name].add_child_group(groups[child_name])

        for group in groups.values():
            if group.depth == 0 and group.name != 'all': # 如果该组深度为0，且名称不是"all"，则加入all组的子组列表中。
                all.add_child_group(group)

        return groups

    def get_host_variables(self, host):
        """ Runs <script> --host <hostname> to determine additional host variables """
        # <script> --host <hostname> 方式获取单个hostname的主机变量
        if self.host_vars_from_top is not None:
            got = self.host_vars_from_top.get(host.name, {}) # 如果存在缓存中，则直接返回
            return got


        cmd = [self.filename, "--host", host.name]
        try:
            sp = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError, e:
            raise errors.AnsibleError("problem running %s (%s)" % (' '.join(cmd), e))
        (out, err) = sp.communicate()
        if out.strip() == '': # 如果输出为空，则返回空字典，否则返回json
            return dict()
        try:
            return json_dict_bytes_to_unicode(utils.parse_json(out))
        except ValueError:
            raise errors.AnsibleError("could not parse post variable response: %s, %s" % (cmd, out))

