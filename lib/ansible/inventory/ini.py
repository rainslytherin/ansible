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

import ansible.constants as C
from ansible.inventory.host import Host
from ansible.inventory.group import Group
from ansible.inventory.expand_hosts import detect_range
from ansible.inventory.expand_hosts import expand_hostname_range
from ansible import errors
from ansible import utils
import shlex
import re
import ast

class InventoryParser(object):
    """
    Host inventory for ansible.
    当inventory文件为ini格式的可读文件时，inventory解析类
    """

    def __init__(self, filename=C.DEFAULT_HOST_LIST):

        with open(filename) as fh:
            self.filename = filename
            # 初始化时将文件内容通过readlines函数赋值给self.lines
            self.lines = fh.readlines()
            self.groups = {}
            self.hosts = {}
            self._parse()

    def _parse(self):

        self._parse_base_groups() # 解析基础group
        self._parse_group_children() # 解析子组
        self._add_allgroup_children() # 将所有主机加入到"all"这个组中
        self._parse_group_variables() # 解析组变量
        return self.groups

    @staticmethod
    def _parse_value(v):
        # 静态方法，用来解析变量值，任何异常情况都以字符串处理
        if "#" not in v:
            try:
                ret = ast.literal_eval(v) # 使用ast.literal_eval 函数取值，v可以是任何Python基本变量类型的值
                if not isinstance(ret, float):
                    # Do not trim floats. Eg: "1.20" to 1.2
                    return ret
            # Using explicit exceptions.
            # Likely a string that literal_eval does not like. We wil then just set it.
            except ValueError:
                # For some reason this was thought to be malformed.
                pass
            except SyntaxError:
                # Is this a hash with an equals at the end?
                pass
        return v

    # [webservers]
    # alpha
    # beta:2345
    # gamma sudo=True user=root
    # delta asdf=jkl favcolor=red

    def _add_allgroup_children(self):
        # 将所有主机加入到“all”这个group中，group.depth==0表示直接包含主机的group，!=all表示非all组
        for group in self.groups.values():
            if group.depth == 0 and group.name != 'all':
                self.groups['all'].add_child_group(group)


    def _parse_base_groups(self):
        # FIXME: refactor，貌似在后面的版本中该函数会被重构
        # 这部分解析ini配置文件的代码却是写的很臃肿，急需要重构
        # 基础group解析，除了已经定义组名的group以外，还有ungrouped表示未分组的组名，all表示所有组的总和

        ungrouped = Group(name='ungrouped')
        all = Group(name='all')
        all.add_child_group(ungrouped)

        # 喜欢这种dict创建字典的方式，比用｛｝的方式好看多了
        self.groups = dict(all=all, ungrouped=ungrouped)
        active_group_name = 'ungrouped'

        # 遍历self.lines，self.lines是文件内容，
        # ini文件的解析大多可以用python内置的conparser类，不过ansible的inventory所支持的语法比较复杂，这里作者自己处理了
        for lineno in range(len(self.lines)):
            line = utils.before_comment(self.lines[lineno]).strip()
            # 如果某行以'['开头，以']'结尾则表名是一个section
            if line.startswith("[") and line.endswith("]"):
                # 将中括号replace掉获得组名
                active_group_name = line.replace("[","").replace("]","")
                # 如果组名中有:vars 或:children，则进行二次处理，比如[southeast:vars]，在通过冒号分割得到southeast
                if ":vars" in line or ":children" in line:
                    active_group_name = active_group_name.rsplit(":", 1)[0] # rsplit(":", 1)表示右边开始以冒号为分隔符分割一次
                    # 如果组名未加到self.groups里面，则创建一个新的Group类，并添加到self.groups里面
                    # 如果组名已经存在与self.groups里面，跳过...
                    if active_group_name not in self.groups:
                        new_group = self.groups[active_group_name] = Group(name=active_group_name)
                    # 在这种情况下将active_group_name设置为None，用来表示这不是一个包含真正host的group
                    active_group_name = None
                # 这部分是组名中没有冒号的处理方式，和上面一样。
                elif active_group_name not in self.groups:
                    new_group = self.groups[active_group_name] = Group(name=active_group_name)
            elif line.startswith(";") or line == '':
                # 如果改行以分号开始或为空行则跳过。
                pass
            elif active_group_name:
                # 这种情况表示当前行为非中括号打头的行，既包含真实host主机数据的行
                # 在section中包含:vars/:children的段并不包含真实主机
                # shlex模块实现了一个类来解析简单的类shell语法，可以用来编写领域特定的语言，或者解析加引号的字符串。
                tokens = shlex.split(line)
                if len(tokens) == 0:
                    continue
                hostname = tokens[0] # 获取主机名
                port = C.DEFAULT_REMOTE_PORT # 使用默认的SSH端口号
                # Three cases to check:
                # 0. A hostname that contains a range pesudo-code and a port,like badwol[a:f].example.com:5309
                # 1. A hostname that contains just a port ,like badwolf.example.com:5309
                # 对hostname需要进行以下的检测
                if hostname.count(":") > 1:
                    # IPV6格式的地址，端口号和hostname之间用"."表示。
                    # Possible an IPv6 address, or maybe a host line with multiple ranges
                    # IPv6 with Port  XXX:XXX::XXX.port
                    # FQDN            foo.example.com
                    if hostname.count(".") == 1:
                        (hostname, port) = hostname.rsplit(".", 1)
                elif ("[" in hostname and
                    "]" in hostname and
                    ":" in hostname and
                    (hostname.rindex("]") < hostname.rindex(":")) or
                    ("]" not in hostname and ":" in hostname)):
                        # 如果冒号在中括号外面，表示这个冒号后面是端口号，因此通过通过rsplit按照冒号分割一次获取端口号和hostname
                        (hostname, port) = hostname.rsplit(":", 1)

                hostnames = []
                # 检测hostname是否是表示一个范围的hosts，如果是则将其扩展成一组host列表，否则加入空列表
                if detect_range(hostname):
                    hostnames = expand_hostname_range(hostname)
                else:
                    hostnames = [hostname]

                # 遍历hostnames列表
                for hn in hostnames:
                    host = None
                    if hn in self.hosts:
                        host = self.hosts[hn]
                    else:
                        # 如果host不在self.hosts列表中，则创建一个Host基类
                        host = Host(name=hn, port=port)
                        self.hosts[hn] = host
                    # len(tokens) > 1表示该行拥有变量，如：jumper ansible_ssh_port=5555 ansible_ssh_host=192.168.1.50
                    if len(tokens) > 1:
                        for t in tokens[1:]:
                            if t.startswith('#'): # 如果是注释则退出，在ini文件中仍然可以使用#作为注释标识。
                                break
                            try:
                                (k,v) = t.split("=", 1) # kv变量解析
                            except ValueError, e:
                                raise errors.AnsibleError("%s:%s: Invalid ini entry: %s - %s" % (self.filename, lineno + 1, t, str(e)))
                            host.set_variable(k, self._parse_value(v)) # 将该行解析的变量设置到该host下
                    self.groups[active_group_name].add_host(host) # 将该host加入到对应的group中。

    # [southeast:children]
    # atlanta
    # raleigh

    def _parse_group_children(self):
        # 在完成子组的添加后，需要对子组进行解析
        group = None

        for lineno in range(len(self.lines)):
            line = self.lines[lineno].strip()
            if line is None or line == '':
                continue
            if line.startswith("[") and ":children]" in line: # 表示改行是子组段
                line = line.replace("[","").replace(":children]","")
                group = self.groups.get(line, None) # 如果group并不存在于self.groups中，则创建新的Group对象，并添加
                if group is None:
                    group = self.groups[line] = Group(name=line)
            elif line.startswith("#") or line.startswith(";"): # 跳过注释
                pass
            elif line.startswith("["): # 其他以中括号开头的行都跳过，并重新初始化group
                group = None
            elif group: # 只有在[xxx:children] 格式后面的行的group才不为None
                kid_group = self.groups.get(line, None)
                if kid_group is None: # 子组必须是已经在之前的步骤中添加到self.groups中
                    raise errors.AnsibleError("%s:%d: child group is not defined: (%s)" % (self.filename, lineno + 1, line))
                else:
                    group.add_child_group(kid_group) # 将该子组加到当前组的子组集合中


    # [webservers:vars]
    # http_port=1234
    # maxRequestsPerChild=200

    def _parse_group_variables(self):
        # 解析组变量
        group = None
        for lineno in range(len(self.lines)):
            line = self.lines[lineno].strip()
            if line.startswith("[") and ":vars]" in line: # [xxx:vars] 格式的行表示组变量section
                line = line.replace("[","").replace(":vars]","")
                group = self.groups.get(line, None) # 如果该组并没有注册到self.groups中，则raise，否则初始化group变量。
                if group is None:
                    raise errors.AnsibleError("%s:%d: can't add vars to undefined group: %s" % (self.filename, lineno + 1, line))
            elif line.startswith("#") or line.startswith(";"): # 跳过注释
                pass
            elif line.startswith("["): # 跳过其他以中括号开头的行，并重新初始化变量group=None.
                group = None
            elif line == '': # 跳过空行
                pass
            elif group: # group对象不为None，则表示改行为有效组变量行。
                if "=" not in line: # 变量必须使用a=b的格式
                    raise errors.AnsibleError("%s:%d: variables assigned to group must be in key=value form" % (self.filename, lineno + 1))
                else:
                    (k, v) = [e.strip() for e in line.split("=", 1)] # 按照“=”分割并去空格
                    group.set_variable(k, self._parse_value(v)) # 将解析后的变量添加到该group的变量中。

    def get_host_variables(self, host):
        # 这个鬼函数。。。。干嘛用的
        return {}
