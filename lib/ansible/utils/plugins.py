# -*- coding: utf-8 -*-
# (c) 2012, Daniel Hokka Zakrisson <daniel@hozac.com>
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

import os
import os.path
import sys
import glob
import imp
from ansible import constants as C
from ansible import errors

MODULE_CACHE = {}
PATH_CACHE = {}
PLUGIN_PATH_CACHE = {}
_basedirs = []

def push_basedir(basedir):
    # avoid pushing the same absolute dir more than once
    basedir = os.path.realpath(basedir)
    if basedir not in _basedirs:
        _basedirs.insert(0, basedir)

class PluginLoader(object):

    '''
    PluginLoader loads plugins from the configured plugin directories.

    It searches for plugins by iterating through the combined list of
    play basedirs, configured paths, and the python path.
    The first match is used.

    # Plugin加载器，遍历play目录、配置路径和Python路径，使用第一个发现的
    '''

    def __init__(self, class_name, package, config, subdir, aliases={}):

        self.class_name         = class_name
        self.package            = package
        self.config             = config
        self.subdir             = subdir
        self.aliases            = aliases

        if not class_name in MODULE_CACHE:
            MODULE_CACHE[class_name] = {}
        if not class_name in PATH_CACHE:
            PATH_CACHE[class_name] = None
        if not class_name in PLUGIN_PATH_CACHE:
            PLUGIN_PATH_CACHE[class_name] = {}

        self._module_cache      = MODULE_CACHE[class_name]
        self._paths             = PATH_CACHE[class_name]
        self._plugin_path_cache = PLUGIN_PATH_CACHE[class_name]

        self._extra_dirs = []
        self._searched_paths = set()

    def print_paths(self):
        ''' Returns a string suitable for printing of the search path '''

        # Uses a list to get the order right
        # 返回搜索路径的字符串表示方式（用冒号连接）
        ret = []
        for i in self._get_paths():
            if i not in ret:
                ret.append(i)
        return os.pathsep.join(ret)

    def _all_directories(self, dir):
        results = []
        results.append(dir)
        for root, subdirs, files in os.walk(dir):
        # os.walk 函数返回一个迭代器，会遍历该目录下的所有子目录和文件，包含隐藏文件
        # 每个迭代结果是一个三元组（目录名，子目录，文件名）
        # 如果该目录下包含"__init__.py"文件，则将该目录下的所有子目录加到result中
        # TODO:这里为什么只判断包含__init__.py文件的目录？
        # 如果该目录包含__init__.py 文件，则表名该目录为可加载的Python目录，因此将子目录路径添加到结果中。
           if '__init__.py' in files:
               for x in subdirs:
                   results.append(os.path.join(root,x))
        return results

    def _get_package_paths(self):
        ''' Gets the path of a Python package '''
        # 得到一个Python package的路径

        paths = []
        if not self.package: # plugin的package name，在plugin初始化时定义，比如action_module的package为：'ansible.runner.action_plugins'
            return []
        if not hasattr(self, 'package_path'):
            m = __import__(self.package)   # 动态加载package，例如，动态加载：'ansible.runner.action_plugins'
            parts = self.package.split('.')[1:]
            # 得到package的完整绝对路径，比如：'/usr/local/lib/python2.7/site-packages/ansible/runner/action_plugins'
            self.package_path = os.path.join(os.path.dirname(m.__file__), *parts)
        paths.extend(self._all_directories(self.package_path))
        return paths

    def _get_paths(self):
        ''' Return a list of paths to search for plugins in '''
        # 返回plugins搜索的路径列表

        if self._paths is not None:
            return self._paths

        # a和a[:]的区别，a是是引用 传址调用，而a[:] 是复制 传值调用
        # b = a 就是直接引用，传址引用，无论修改a、b任何一个变量的值，另外一个都会响应变化
        # b = a[:] 和 b = list(a) 或 b = copy.copy(a) 都实现的是浅拷贝，创建一个新的对象，其内容是原对象中元素的引用。
        # 1、赋值：简单地拷贝对象的引用，两个对象的id相同。
        # 2、浅拷贝：创建一个新的组合对象，这个新对象与原对象共享内存中的子对象。
        # 3、深拷贝：创建一个新的组合对象，同时递归地拷贝所有子对象，新的组合对象与原对象没有任何关联。虽然实际上会共享不可变的子对象，但不影响它们的相互独立性。
        # 浅拷贝和深拷贝的不同仅仅是对组合对象来说，所谓的组合对象就是包含了其它对象的对象，如列表，类实例。而对于数字、字符串以及其它“原子”类型，没有拷贝一说，产生的都是原对象的引用。
        """
            >>> import copy
            >>> a = [1,2,3,[4,5]]
            >>>
            >>> b = a
            >>> c = a[:]
            >>> d = list(a)
            >>> e = copy.copy(a)
            >>> f = copy.deepcopy(a)
            >>>
            >>> a[0] = 100
            >>> a[3].append(6)

            result:
            >>> print a
            [100, 2, 3, [4, 5, 6]]
            >>> print b
            [100, 2, 3, [4, 5, 6]]
            >>> print c
            [1, 2, 3, [4, 5, 6]]
            >>> print d
            [1, 2, 3, [4, 5, 6]]
            >>> print e
            [1, 2, 3, [4, 5, 6]]
            >>> print f
            [1, 2, 3, [4, 5]]

            >>> print id(a),id(b),id(c),id(e),id(f)
            248793568 248793568 248794144 248035016 248807720
            >>> print id(a[3]),id(b[3]),id(c[3]),id(d[3]),id(e[3]),id(f[3])
            248795792 248795792 248795792 248795792 248795792 248795216
        """
        ret = self._extra_dirs[:]
        # 遍历basedir目录，我的ansible目录一般是：/home/xinyu.zhao/ansible
        # 没有开发自定义的action_plugin时，则跳过
        for basedir in _basedirs:
            fullpath = os.path.realpath(os.path.join(basedir, self.subdir))
            # 得到子目录的绝对路径，子目录可能是plugin目录，比如action_plugins，那么子目录的绝对路径：'/home/apps/ansible/action_plugins'

            if os.path.isdir(fullpath): # 如果子目录存在，则继续查找，否则跳过；

                files = glob.glob("%s/*" % fullpath) # Python glob模块用于使用正则查找文件，插在fullpath下的所有文件列表

                # allow directories to be two levels deep
                files2 = glob.glob("%s/*/*" % fullpath) # fullpath/*/* 二级目录的所有文件列表

                if files2 is not None:
                    files.extend(files2) # 合并列表

                for file in files:
                    if os.path.isdir(file) and file not in ret:
                        ret.append(file)
                if fullpath not in ret:
                    ret.append(fullpath)

        # look in any configured plugin paths, allow one level deep for subcategories
        #　获取配置路径的所有文件路径
        # 以action_plugin为例，结果为：
        # /usr/share/ansible_plugins/action_plugins
        if self.config is not None:
            configured_paths = self.config.split(os.pathsep) #ansible.cfg中使用冒号分割目录
            for path in configured_paths:
                path = os.path.realpath(os.path.expanduser(path))
                contents = glob.glob("%s/*" % path) + glob.glob("%s/*/*" % path) # 查找该目录下的一级二级目录
                for c in contents:
                    if os.path.isdir(c) and c not in ret:
                        ret.append(c)
                if path not in ret:
                    ret.append(path)


        # look for any plugins installed in the package subtree
        # 获取ansible package中的所有路径
        # 以action_plugin为例，结果为：
        # ret = '/usr/local/lib/python2.7/site-packages/ansible/runner/action_plugins'
        ret.extend(self._get_package_paths())

        # 上述三个步骤的结果为：
        # ret = ['/usr/share/ansible_plugins/action_plugins', '/usr/local/lib/python2.7/site-packages/ansible/runner/action_plugins']
        # cache and return the result
        # 缓存并返回结果
        self._paths = ret
        return ret


    def add_directory(self, directory, with_subdir=False):
        ''' Adds an additional directory to the search path '''
        # 将某个目录添加到搜索路径中

        directory = os.path.realpath(directory)

        if directory is not None:
            if with_subdir:
                directory = os.path.join(directory, self.subdir)
            if directory not in self._extra_dirs:
                # append the directory and invalidate the path cache
                self._extra_dirs.append(directory)
                self._paths = None

    def find_plugin(self, name, suffixes=None):
        ''' Find a plugin named name '''
        # 找到对应名称的plugin
        if not suffixes:
            if self.class_name:
                suffixes = ['.py']
            else:
                suffixes = ['.py', '']

        try:
            return self._plugin_path_cache[name] # 查找插件路径缓存，如果有则直接返回，否则继续查找
        except KeyError:
            # Cache miss.  Now let's find the the plugin
            pass

        for path in [p for p in self._get_paths() if p not in self._searched_paths]:
            if os.path.isdir(path): # 判断路径是否是目录，且是否存在
                # 获取目录下的所有文件的绝对路径
                full_paths = (os.path.join(path, f) for f in os.listdir(path))
                # 遍历目录中的文件
                for full_path in (f for f in full_paths if os.path.isfile(f)):
                    for suffix in suffixes:
                    #判断文件是否是.py结尾的（如果没有定义suffixes）
                        if full_path.endswith(suffix):
                            full_name = os.path.basename(full_path)
                            if suffix:
                                base_name = full_name[:-len(suffix)] # 如果文件有后缀，去掉后缀名
                            else:
                                base_name = full_name
                            break
                    else: # Yes, this is a for-else: http://bit.ly/1ElPkyg
                        continue

                    # Module found, now see if it's already in the cache
                    # 如果模块没有被缓存，则加到self._plugin_path_cache中
                    # self._plugin_path_cache的结果大概是这个样子的：
                    # 以模块名称为key，模块的完整文件路径为value，老版本是以"模块名.py"为key
                    '''
                        新版本：
                        {
                            'patch': '/usr/local/lib/python2.7/site-packages/ansible/runner/action_plugins/patch.py',
                            'add_host': '/usr/local/lib/python2.7/site-packages/ansible/runner/action_plugins/add_host.py'
                        }

                        老版本：
                        {
                            'patch.py': '/usr/local/lib/python2.7/site-packages/ansible/runner/action_plugins/patch.py',
                            'add_host.py': '/usr/local/lib/python2.7/site-packages/ansible/runner/action_plugins/add_host.py'
                        }
                    '''
                    if base_name not in self._plugin_path_cache:
                        self._plugin_path_cache[base_name] = full_path

            self._searched_paths.add(path) # 把该path加到已经搜索过的set里
            try:
                return self._plugin_path_cache[name] # 如果缓存中存在则返回并退出
            except KeyError:
                # Didn't find the plugin in this directory.  Load modules from
                # the next one
                # 如果当前的目录中没有发现plugin，则处理下一个
                pass

        # if nothing is found, try finding alias/deprecated
        if not name.startswith('_'):
            alias_name = '_' + name
            # We've already cached all the paths at this point
            if alias_name in self._plugin_path_cache:
                return self._plugin_path_cache[alias_name]

        return None

    def has_plugin(self, name):
        ''' Checks if a plugin named name exists '''

        return self.find_plugin(name) is not None

    __contains__ = has_plugin

    def get(self, name, *args, **kwargs):
        ''' instantiates a plugin of the given name using arguments '''

        if name in self.aliases: # 如果是别名，则使用别名字典的value
            name = self.aliases[name]
        path = self.find_plugin(name)
        if path is None:
            return None
        if path not in self._module_cache:
            self._module_cache[path] = imp.load_source('.'.join([self.package, name]), path)
        return getattr(self._module_cache[path], self.class_name)(*args, **kwargs)

    def all(self, *args, **kwargs):
        ''' instantiates all plugins with the same arguments '''

        for i in self._get_paths():
            matches = glob.glob(os.path.join(i, "*.py"))
            matches.sort()
            for path in matches:
                name, ext = os.path.splitext(os.path.basename(path))
                if name.startswith("_"):
                    continue
                if path not in self._module_cache:
                    self._module_cache[path] = imp.load_source('.'.join([self.package, name]), path)
                yield getattr(self._module_cache[path], self.class_name)(*args, **kwargs)

# 在这里定义action_loader
# class_name: ActionModule
# package: ansible.runner.action_plugins
# config: C.DEFAULT_ACTION_PLUGIN_PATH,配置文件中的默认配置：'/usr/share/ansible_plugins/action_plugins'
# subdir: action_plugins，子目录是action_plugins
# 下面几个类似

action_loader = PluginLoader(
    'ActionModule',
    'ansible.runner.action_plugins',
    C.DEFAULT_ACTION_PLUGIN_PATH,
    'action_plugins'
)

cache_loader = PluginLoader(
    'CacheModule',
    'ansible.cache',
    C.DEFAULT_CACHE_PLUGIN_PATH,
    'cache_plugins'
)

callback_loader = PluginLoader(
    'CallbackModule',
    'ansible.callback_plugins',
    C.DEFAULT_CALLBACK_PLUGIN_PATH,
    'callback_plugins'
)

connection_loader = PluginLoader(
    'Connection',
    'ansible.runner.connection_plugins',
    C.DEFAULT_CONNECTION_PLUGIN_PATH,
    'connection_plugins',
    aliases={'paramiko': 'paramiko_ssh'}
)

shell_loader = PluginLoader(
    'ShellModule',
    'ansible.runner.shell_plugins',
    'shell_plugins',
    'shell_plugins',
)

module_finder = PluginLoader(
    '',
    'ansible.modules',
    C.DEFAULT_MODULE_PATH,
    'library'
)

lookup_loader = PluginLoader(
    'LookupModule',
    'ansible.runner.lookup_plugins',
    C.DEFAULT_LOOKUP_PLUGIN_PATH,
    'lookup_plugins'
)

vars_loader = PluginLoader(
    'VarsModule',
    'ansible.inventory.vars_plugins',
    C.DEFAULT_VARS_PLUGIN_PATH,
    'vars_plugins'
)

filter_loader = PluginLoader(
    'FilterModule',
    'ansible.runner.filter_plugins',
    C.DEFAULT_FILTER_PLUGIN_PATH,
    'filter_plugins'
)

fragment_loader = PluginLoader(
    'ModuleDocFragment',
    'ansible.utils.module_docs_fragments',
    os.path.join(os.path.dirname(__file__), 'module_docs_fragments'),
    '',
)
