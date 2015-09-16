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

import ansible.constants as C
from ansible import utils

class Host(object):
    ''' a single ansible host '''
    # Host对象基类

    # __slots__ 限制属性访问
    __slots__ = [ 'name', 'vars', 'groups' ]

    def __init__(self, name=None, port=None):
        '''
            Host对象，包含如下属性：
            name，主机名称
            vars，主机变量
            groups，主机所属组
            port，端口号，远程访问使用，默认使用22.
        '''

        self.name = name
        self.vars = {}
        self.groups = []
        if port and port != C.DEFAULT_REMOTE_PORT: # 如果不使用默认端口号，则重新设置
            self.set_variable('ansible_ssh_port', int(port))

        if self.name is None:
            raise Exception("host name is required")

    def add_group(self, group):
        # 将当前host对象添加到某个group中，由于该函数只在Group类的add_host函数中调用，故因此不需要再重复调用Group的add_host方法。
        self.groups.append(group)

    def set_variable(self, key, value):
        # 设置变量值
        self.vars[key]=value

    def get_groups(self):
        # 获取当前host的所有group，需要分为两步：1.获取当前host直接所属组；2.获取所属组的所有父组。
        groups = {}
        for g in self.groups:
            groups[g.name] = g
            ancestors = g.get_ancestors()
            for a in ancestors:
                groups[a.name] = a
        return groups.values()

    def get_variables(self):
        # 获取当前host的变量值
        results = {}
        groups = self.get_groups() # 获取所有的父组
        for group in sorted(groups, key=lambda g: g.depth): # 根据组的深度对组进行排序
            # 我刚还在想group的深度有什么用，原来用在了这里，根据深度排序后，如果出现相同的组变量，则以深度最浅的组为准。
            results = utils.combine_vars(results, group.get_variables())
        # host变量的优先级高于组变量，如果出现相同的变量，则以host中的变量为准。
        results = utils.combine_vars(results, self.vars)
        results['inventory_hostname'] = self.name # inventory_hostname为可以跨host访问的变量
        results['inventory_hostname_short'] = self.name.split('.')[0] # inventory_hostname_short可以跨host访问
        results['group_names'] = sorted([ g.name for g in groups if g.name != 'all']) # group_names也可以跨host访问
        return results


