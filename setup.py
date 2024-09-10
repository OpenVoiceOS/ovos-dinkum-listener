# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import os
import os.path
from pathlib import Path

from setuptools import setup


BASEDIR = os.path.abspath(os.path.dirname(__file__))


def package_files(directory):
    paths = []
    for (path, _, filenames) in os.walk(directory):
        for filename in filenames:
            paths.append(os.path.join('..', path, filename))
    return paths


def required(requirements_file):
    """ Read requirements file and remove comments and empty lines. """
    with open(os.path.join(BASEDIR, requirements_file), 'r') as f:
        requirements = f.read().splitlines()
        if 'MYCROFT_LOOSE_REQUIREMENTS' in os.environ:
            print('USING LOOSE REQUIREMENTS!')
            requirements = [r.replace('==', '>=').replace('~=', '>=') for r in requirements]
        return [pkg for pkg in requirements
                if pkg.strip() and not pkg.startswith("#")]


def get_version():
    """ Find the version of ovos-core"""
    version = None
    version_file = os.path.join(BASEDIR, 'ovos_dinkum_listener', 'version.py')
    major, minor, build, alpha = (None, None, None, None)
    with open(version_file) as f:
        for line in f:
            if 'VERSION_MAJOR' in line:
                major = line.split('=')[1].strip()
            elif 'VERSION_MINOR' in line:
                minor = line.split('=')[1].strip()
            elif 'VERSION_BUILD' in line:
                build = line.split('=')[1].strip()
            elif 'VERSION_ALPHA' in line:
                alpha = line.split('=')[1].strip()

            if ((major and minor and build and alpha) or
                    '# END_VERSION_BLOCK' in line):
                break
    version = f"{major}.{minor}.{build}"
    if int(alpha):
        version += f"a{alpha}"
    return version


def get_description():
    with open(os.path.join(BASEDIR, "README.md"), "r") as f:
        long_description = f.read()
    return long_description


setup(
    name='ovos-dinkum-listener',
    version=get_version(),
    license='Apache-2.0',
    url='https://github.com/OpenVoiceOS/ovos-dinkum-listener',
    description='ovos-core listener daemon client',
    long_description=get_description(),
    long_description_content_type="text/markdown",
    packages=['ovos_dinkum_listener'],
    package_data={'': package_files('ovos_dinkum_listener')},
    include_package_data=True,
    install_requires=required('requirements/requirements.txt'),
    extras_require={
        "extras": required("requirements/extras.txt")
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Apache Software License",
    ],
    entry_points={
        'console_scripts': [
            'ovos-dinkum-listener=ovos_dinkum_listener.__main__:main'
        ]
    }
)
