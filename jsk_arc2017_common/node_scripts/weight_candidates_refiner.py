#!/usr/bin/env python

from jsk_arc2017_common.msg import WeightStamped
from jsk_recognition_msgs.msg import Label
from jsk_recognition_msgs.msg import LabelArray
from jsk_topic_tools import ConnectionBasedTransport
import message_filters
import rospy
from std_srvs.srv import Trigger
from std_srvs.srv import TriggerResponse


class WeightCanditatesRefiner(ConnectionBasedTransport):

    def __init__(self):
        super(WeightCanditatesRefiner, self).__init__()
        # {object_name: object_id}
        self.candidates = {}
        self.input_topics = rospy.get_param('~input_topics')
        # {object_name: weight}
        self.object_weights = rospy.get_param('~object_weights')
        self.error = rospy.get_param('~error', 1.0)

        self.weight_sum = 0.0
        self.prev_weight_values = [0] * len(self.input_topics)

        self.weight_sum_pub = self.advertise(
            '~output/weight_sum', WeightStamped, queue_size=1)
        self.picked_pub = self.advertise(
            '~output/candidates/picked', LabelArray, queue_size=1)
        self.placed_pub = self.advertise(
            '~output/candidates/placed', LabelArray, queue_size=1)
        self.init_srv = rospy.Service('~reset', Trigger, self._reset)

    def subscribe(self):
        use_async = rospy.get_param('~approximate_sync', False)
        queue_size = rospy.get_param('~queue_size', 10)
        # add candidates subscriber
        self.sub_candidates = rospy.Subscriber(
            '~input/candidates', LabelArray, self._candidates_cb)
        # add scale subscriber
        self.subs = []
        for input_topic in self.input_topics:
            sub = message_filters.Subscriber(input_topic, WeightStamped)
            self.subs.append(sub)
        if use_async:
            slop = rospy.get_param('~slop', 0.1)
            sync = message_filters.ApproximateTimeSynchronizer(
                self.subs, queue_size=queue_size, slop=slop)
        else:
            sync = message_filters.TimeSynchronizer(
                self.subs, queue_size=queue_size)
        sync.registerCallback(self._scale_cb)

    def unsubscribe(self):
        self.sub_candidates.unregister()
        for sub in self.subs:
            sub.unregister()

    def _candidates_cb(self, labels_msg):
        self.candidates = {}
        for label_msg in labels_msg.labels:
            self.candidates[label_msg.name] = label_msg.id

    def _scale_cb(self, *weight_msgs):
        if not self.candidates:
            rospy.logwarn_throttle(10, 'No candidates, so skipping')
            return
        candidates = self.candidates

        assert len(weight_msgs) == len(self.prev_weight_values)
        weight_values = [w.weight.value for w in weight_msgs]
        if any(w < 0 for w in weight_values):
            return  # unstable, scale over, or something
        self.prev_weight_values = weight_values

        weight_sum = sum(weight_values)
        weight_diff = weight_sum - self.weight_sum
        sum_msg = WeightStamped()
        sum_msg.header = weight_msgs[0].header
        sum_msg.weight.value = weight_sum

        pick_msg = LabelArray()
        place_msg = LabelArray()
        pick_msg.header = weight_msgs[0].header
        place_msg.header = weight_msgs[0].header
        diff_lower = weight_diff - self.error
        diff_upper = weight_diff + self.error
        for obj_name, w in self.object_weights.items():
            if obj_name not in candidates:
                continue
            obj_id = candidates[obj_name]
            if diff_upper > w and w > diff_lower:
                label = Label()
                label.id = obj_id
                label.name = obj_name
                place_msg.labels.append(label)
            elif diff_upper > -w and -w > diff_lower:
                label = Label()
                label.id = obj_id
                label.name = obj_name
                pick_msg.labels.append(label)

        self.weight_sum_pub.publish(sum_msg)
        self.picked_pub.publish(pick_msg)
        self.placed_pub.publish(place_msg)

    def _reset(self, req):
        is_success = True
        try:
            self.weight_sum = sum(self.prev_weight_values)
        except Exception:
            is_success = False
        return TriggerResponse(success=is_success)


if __name__ == '__main__':
    rospy.init_node('weight_candidates_refiner')
    app = WeightCanditatesRefiner()
    rospy.spin()