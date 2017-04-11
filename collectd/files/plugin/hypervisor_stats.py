#!/usr/bin/python
# Copyright 2015 Mirantis, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Collectd plugin for getting hypervisor statistics from Nova
if __name__ == '__main__':
    import collectd_fake as collectd
else:
    import collectd

import collectd_openstack as openstack

PLUGIN_NAME = 'openstack_nova'
INTERVAL = openstack.INTERVAL


class HypervisorStatsPlugin(openstack.CollectdPlugin):
    """ Class to report the statistics on Nova hypervisors."""
    VALUE_MAP = {
        'current_workload': 'running_tasks',
        'running_vms': 'running_instances',
        'local_gb_used': 'used_disk',
        'free_disk_gb': 'free_disk',
        'memory_mb_used': 'used_ram',
        'free_ram_mb': 'free_ram',
        'vcpus_used': 'used_vcpus',
    }
    UNIT_MAP = {
        'local_gb_used': 'GB',
        'free_disk_gb': 'GB',
        'memory_mb_used': 'MB',
        'free_ram_mb': 'MB',
    }

    def __init__(self, *args, **kwargs):
        super(HypervisorStatsPlugin, self).__init__(*args, **kwargs)
        self.plugin = PLUGIN_NAME
        self.interval = INTERVAL

    def config_callback(self, config):
        super(HypervisorStatsPlugin, self).config_callback(config)
        for node in config.children:
            if node.key == 'CpuAllocationRatio':
                self.extra_config['cpu_ratio'] = float(node.values[0])
        if 'cpu_ratio' not in self.extra_config:
            self.logger.warning('CpuAllocationRatio parameter not set')

    @classmethod
    def initialize_metrics(self):
        metrics = {v: 0 for v in self.VALUE_MAP.values()}
        metrics['free_vcpus'] = 0
        return metrics

    def itermetrics(self):
        nova_aggregates = {}
        r = self.get('nova', 'os-aggregates')
        if not r:
            self.logger.warning("Could not get nova aggregates")
        else:
            aggregates_list = r.json().get('aggregates', [])
            for agg in aggregates_list:
                nova_aggregates[agg['name']] = {
                    'id': agg['id'],
                    'hosts': [h.split('.')[0] for h in agg['hosts']],
                    'metrics': self.initialize_metrics()
                }

        r = self.get('nova', 'os-hypervisors/detail')
        if not r:
            self.logger.warning("Could not get hypervisor statistics")
            return

        total_stats = self.initialize_metrics()
        hypervisor_stats = r.json().get('hypervisors', [])
        for stats in hypervisor_stats:
            # remove domain name and keep only the hostname portion
            host = stats['hypervisor_hostname'].split('.')[0]
            for k, v in self.VALUE_MAP.iteritems():
                m_val = stats.get(k, 0)
                meta = {'hostname': host}
                if k in self.UNIT_MAP:
                    meta['unit'] = self.UNIT_MAP[k]
                yield {
                    'plugin_instance': v,
                    'values': m_val,
                    'meta': meta
                }
                total_stats[v] += m_val
                for agg in nova_aggregates.keys():
                    agg_hosts = nova_aggregates[agg]['hosts']
                    if host in agg_hosts:
                        nova_aggregates[agg]['metrics'][v] += m_val
            if 'cpu_ratio' in self.extra_config:
                m_vcpus = stats.get('vcpus', 0)
                m_vcpus_used = stats.get('vcpus_used', 0)
                free = (int(self.extra_config['cpu_ratio'] *
                        m_vcpus)) - m_vcpus_used
                yield {
                    'plugin_instance': 'free_vcpus',
                    'values': free,
                    'meta': {'hostname': host},
                }
                total_stats['free_vcpus'] += free
                for agg in nova_aggregates.keys():
                    agg_hosts = nova_aggregates[agg]['hosts']
                    if host in agg_hosts:
                        free = ((int(self.extra_config['cpu_ratio'] *
                                     m_vcpus)) -
                                m_vcpus_used)
                        nova_aggregates[agg]['metrics']['free_vcpus'] += free

        # Dispatch metrics for every aggregate
        for agg in nova_aggregates.keys():
            agg_id = nova_aggregates[agg]['id']
            agg_total_free_ram = (
                nova_aggregates[agg]['metrics']['free_ram'] +
                nova_aggregates[agg]['metrics']['used_ram']
            )
            # Only emit metric when the value is > 0 to avoid division by zero
            if agg_total_free_ram > 0:
                nova_aggregates[agg]['metrics']['free_ram_percent'] = round(
                    (100.0 * nova_aggregates[agg]['metrics']['free_ram']) /
                    agg_total_free_ram,
                    2)
            for k, v in nova_aggregates[agg]['metrics'].iteritems():
                yield {
                    'plugin_instance': 'aggregate_{}'.format(k),
                    'values': v,
                    'meta': {
                        'aggregate': agg,
                        'aggregate_id': agg_id,
                        'meta': {'discard_hostname': True}
                    }
                }
        # Dispatch the global metrics
        for k, v in total_stats.iteritems():
            yield {
                'type_instance': 'total_{}'.format(k),
                'values': v,
                'meta': {'discard_hostname': True}
            }

plugin = HypervisorStatsPlugin(collectd, PLUGIN_NAME,
                               disable_check_metric=True)


def config_callback(conf):
    plugin.config_callback(conf)


def notification_callback(notification):
    plugin.notification_callback(notification)


def read_callback():
    plugin.conditional_read_callback()

if __name__ == '__main__':
    collectd.load_configuration(plugin)
    plugin.read_callback()
else:
    collectd.register_config(config_callback)
    collectd.register_notification(notification_callback)
    collectd.register_read(read_callback, INTERVAL)
