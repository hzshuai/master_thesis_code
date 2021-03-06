"""
Creates PROTOTXT files for a multiscale accumulator network in Caffe. The input is a configuration
file with the network description. This is a sample:
my_beautiful_network
r1 c0.3
conv k3      o64
conv k3  d2  o64
pool
conv k3      o128
conv k3  d2  o128
pool
conv k3      o256
conv k3  d2  o256
macc x4

----------------------------------------------------------------------------------------------------
python macc_net_generator.py path/to/config.txt path/to/output/folder
----------------------------------------------------------------------------------------------------
"""

__date__   = '02/26/2017'
__author__ = 'Libor Novak'
__email__  = 'novakli2@fel.cvut.cz'

import argparse
import os
from math import ceil


####################################################################################################
#                                            FUNCTIONS                                             # 
####################################################################################################

def get_value_float(data, id, required=False):
	"""
	Finds a value with the given id among data and returns its value. The data are strings with one
	letters (id), followed by numbers. There are no spaces.

	Input:
		data: List of strings (something like ['o1.2', 'r45', 't0.9'])
		id:   Id of the item to find - its string id
	"""
	for val in data:
		if id in val:
			return float(val[len(id):])

	if required:
		print('ERROR: "' + id + '" is required in ' + str(data) + '!')
		exit()

	return None


def get_value_int(data, id, required=False):
	"""
	Finds a value with the given id among data and returns its value. The data are strings with one
	letters (id), followed by numbers. There are no spaces.

	Input:
		data: List of strings (something like ['o12', 'r45', 't9'])
		id:   Id of the item to find - its string id
	"""
	val = get_value_float(data, id, required)
	return int(val) if val is not None else None



####################################################################################################
#                                             CLASSES                                              # 
####################################################################################################

class MACCNetGenerator(object):
	def __init__(self, path_config, bb_type):
		"""
		Input:
			path_config: Path to a configuration file with net structure
			bb_type:     Type of data and loss layers ('bbtxt', 'bb3txt')
		"""
		self.path_config = path_config
		self.bb_type     = bb_type

		self.reset()


	def reset(self):
		self.previous_layer = 'data'
		self.downsampling = 1
		self.new_conv_id = 1
		self.last_in_scale = {}
		self.last_in_scale_fov = {}
		self.fov_base = 1
		self.fov_previous = 1
		self.fov_prev_downsampling = 1
		self.accs = []
		self.acc_scales = []
		self.acc_bbs_ideal = []


	def generate_prototxt_files(self, path_out):
		"""
		"""
		if not os.path.exists(path_out):
			os.makedirs(path_out)

		lines = []

		# Parse the configuration file and create the train_val.prototxt and deploy.protoxt files
		with open(self.path_config, 'r') as infile:
			# First line contains the name of the network
			self.name = infile.readline().rstrip('\n')

			# Second line contains the radius of the circle in the accumulator and the circle
			# ratio - the size of the circle in the accumulator with respect to max(w,h) of
			# a bounding box
			data = infile.readline().rstrip('\n').split()
			self.radius       = get_value_int(data, 'r', required=True)
			self.circle_ratio = get_value_float(data, 'c', required=True)
			
			for line in infile:
				lines.append(line.rstrip('\n'))
				print(line.rstrip('\n'))


		# Create the train_val.prototxt file
		print('\n-- TRAIN_VAL')
		with open(os.path.join(path_out, self.name + '_train_val.prototxt'), 'w') as outfile:
			self.reset()

			outfile.write('name: "' + self.name + '"\n\n')
			outfile.write(self._layer_data('TRAIN'))
			outfile.write(self._layer_data('TEST'))
			
			outfile.write('\n# ' + '-'*38 + ' NETWORK STRUCTURE ' + '-'*39 + ' #\n')
			outfile.write(self._downsampling())
			
			for line in lines:
				self._add_layer(line, outfile, False)
			
			outfile.write(self._layer_loss())


		# Create the deploy.prototxt file
		print('\n-- DEPLOY')
		with open(os.path.join(path_out, self.name + '_deploy.prototxt'), 'w') as outfile:
			self.reset()

			outfile.write('name: "' + self.name + '"\n\n')
			outfile.write(self._layer_input())
			
			outfile.write('\n# ' + '-'*38 + ' NETWORK STRUCTURE ' + '-'*39 + ' #\n')
			outfile.write(self._downsampling())
			
			for line in lines:
				self._add_layer(line, outfile, True)

			outfile.write(self._layer_bb())


	################################################################################################
	#                                          PRIVATE                                             #
	################################################################################################

	def _add_layer(self, line, outfile, deploy):
		"""
		Adds one layer to the PROTOTXT file specified by the line.

		Input:
			line: string with layer description (one line from the config file)
			outfile: File handle into which we will write the layer
			deploy: True/False
		"""
		layer_type = line[:4]

		if layer_type == 'conv':
			# Convolutional layer
			outfile.write(self._layer_conv(line, deploy))
			outfile.write(self._layer_relu())
		elif layer_type == 'pool':
			# Pooling layer
			outfile.write(self._layer_pool())
		elif layer_type == 'macc':
			# Multiscale accumulator - this is also a convolutional layer, but with
			# 1 output channel
			outfile.write(self._layer_macc(line, deploy))


	def _layer_relu(self):
		"""
		Creates description of a ReLU layer.
		"""
		return ('layer {\n' \
				'  name: "relu_' + self.previous_layer + '"\n' \
				'  type: "ReLU"\n' \
				'  bottom: "' + self.previous_layer + '"\n' \
				'  top: "' + self.previous_layer + '"\n' \
				'}\n')


	def _layer_conv(self, specs, deploy=False):
		"""
		Creates a description of a convolutional layer.

		Input:
			specs: string (one line from the config file) with the layer description
			deploy: True/False - includes or does not include weight filling
		"""
		name = 'conv_x%d_%d'%(self.downsampling, self.new_conv_id)
		self.new_conv_id += 1

		# Parse specs
		data = specs[5:].split()
		num_output  = get_value_int(data, 'o', required=True)
		kernel_size = get_value_int(data, 'k', required=True)
		dilation    = get_value_int(data, 'd')


		# Compute the padding
		pad = (kernel_size-1) / 2
		if dilation is not None:
			pad = ((kernel_size-1) / 2) * (dilation+1)

		# Field of view computation
		if dilation is not None:
			self.fov_base = self.fov_base-1 + ((dilation+1)*(kernel_size-1) + 1)
		else:
			self.fov_base = self.fov_base-1 + kernel_size

		fov = self.fov_base * self.downsampling + self.fov_prev_downsampling-ceil(self.downsampling/2.0)
		self.fov_previous = fov

		print('-- ' + name +  ' \t FOV %d x %d'%(fov, fov))

		out  = ('layer {\n' \
				'  # ' + '-'*23 + '  FOV %d x %d  (%d+%d=%d)\n'%(fov, fov, self.fov_base * self.downsampling, self.fov_prev_downsampling-ceil(self.downsampling/2.0), fov) + \
				'  name: "' + name + '"\n' \
				'  type: "Convolution"\n' \
				'  bottom: "' + self.previous_layer + '"\n' \
				'  top: "' + name + '"\n')

		if not deploy:
			out += ('  param {\n' \
					'    lr_mult: 1\n' \
					'    decay_mult: 1\n' \
					'  }\n' \
					'  param {\n' \
					'    lr_mult: 2\n' \
					'    decay_mult: 0\n' \
					'  }\n')

		out += ('  convolution_param {\n' \
				'    num_output: %d\n'%(num_output) + \
				'    kernel_size: %d\n'%(kernel_size))
		if pad is not None:
			out +=	'    pad: %d\n'%(pad)
		if dilation is not None:
			out += '    dilation: %d\n'%(dilation+1)

		if not deploy:
			out += ('    weight_filler {\n' \
					'      type: "xavier"\n' \
					'    }\n' \
					'    bias_filler {\n' \
					'      type: "constant"\n' \
					'      value: 0\n' \
					'    }\n')

		out += ('  }\n' \
				'}\n')

		self.previous_layer = name
		self.last_in_scale[self.downsampling] = name
		self.last_in_scale_fov[self.downsampling] = fov

		return out


	def _layer_pool(self):
		"""
		Create a description of a pooling layer. When a pooling layer is created the downsampling
		automatically increases.
		"""
		# Pooling layer downsamples 2x the image
		self.downsampling *= 2
		# Restart the ids of the convolution layers
		self.new_conv_id = 1
		self.fov_base = 1
		self.fov_prev_downsampling = self.fov_previous

		name = 'pool_x%d'%(self.downsampling)

		print('-- Pool')

		out  = self._downsampling()
		out += ('layer {\n' \
				'  name: "' + name + '"\n' \
				'  type: "Pooling"\n' \
				'  bottom: "' + self.previous_layer + '"\n' \
				'  top: "' + name + '"\n' \
				'  pooling_param {\n' \
				'    pool: MAX\n' \
				'    kernel_size: 2\n' \
				'    stride: 2\n' \
				'  }\n' \
				'}\n')

		self.previous_layer = name

		return out


	def _layer_macc(self, specs, deploy=False):
		"""
		Creates a description of an accumulator layer from the specs.

		Input:
			specs: string (line from the config file) with the layer description
		"""
		data = specs[5:].split()
		scale  = get_value_int(data, 'x', required=True)

		if scale not in self.last_in_scale:
			print('ERROR: Accumulator of this scale cannot be created "' + specs + '"!')
			exit()

		name = 'acc_x%d'%(scale)
		bb_ideal = (2*self.radius+1) * scale * 1/self.circle_ratio

		print('-- ' + name + ' \t SCALE 1/%d  (FOV %d x %d, BB %dx%d px)'%(scale, self.last_in_scale_fov[scale], self.last_in_scale_fov[scale], bb_ideal, bb_ideal))

		out  = ('layer {\n' \
				'  # -----------------------  ACCUMULATOR\n' \
				'  # -----------------------  SCALE 1/%d  (FOV %d x %d)\n'%(scale, self.last_in_scale_fov[scale], self.last_in_scale_fov[scale]) + \
				'  # -----------------------  Ideal bounding box size: %dx%d px\n'%(bb_ideal, bb_ideal) + \
				'  name: "' + name + '"\n' \
				'  type: "Convolution"\n' \
				'  bottom: "' + self.last_in_scale[scale] + '"\n' \
				'  top: "' + name + '"\n')

		if not deploy:
			out += ('  param {\n' \
					'    lr_mult: 1\n' \
					'    decay_mult: 1\n' \
					'  }\n' \
					'  param {\n' \
					'    lr_mult: 2\n' \
					'    decay_mult: 0\n' \
					'  }\n')

		out += ('  convolution_param {\n' \
				'    num_output: ' + ('8' if self.bb_type == 'bb3txt' else '5') + '\n' \
				'    kernel_size: 1\n')

		if not deploy:
			out += ('    weight_filler {\n' \
					'      type: "xavier"\n' \
					'    }\n' \
					'    bias_filler {\n' \
					'      type: "constant"\n' \
					'      value: 0\n' \
					'    }\n')

		out += ('  }\n' \
				'}\n')

		# List of accumulators - for the loss layer
		self.accs.append(name)
		self.acc_scales.append(scale)
		self.acc_bbs_ideal.append(bb_ideal)

		return out


	def _downsampling(self):
		"""
		Prints the current downsampling factor.
		"""
		return ('# ' + '-'*45 + ' x%3d '%(self.downsampling) + '-'*45 + ' #\n')


	def _layer_loss(self):
		"""
		Description of the MultiscaleAccumulatorLoss layer.
		"""
		out = '\n# ' + '-'*45 + ' LOSS ' + '-'*45 + ' #\n'

		for i in range(len(self.accs)):
			out += ('layer {\n' \
					'  # -----------------------  SCALE 1/%d  (FOV %d x %d)\n'%(self.acc_scales[i], self.last_in_scale_fov[self.acc_scales[i]], self.last_in_scale_fov[self.acc_scales[i]]) + \
					'  # -----------------------  Ideal bounding box size: %dx%d px\n'%(self.acc_bbs_ideal[i], self.acc_bbs_ideal[i]) + \
					'  name: "loss_x%d"\n'%(self.acc_scales[i]) + \
					'  type: "BB' + ('3' if self.bb_type == 'bb3txt' else '') + 'TXTLoss"\n' \
					'  bottom: "label"\n'
					'  bottom: "' + self.accs[i] + '"\n'
					'  top: "loss_x%d"\n'%(self.acc_scales[i]) + \
					'  accumulator_loss_param {\n' \
					'    radius: %d\n'%(self.radius) + \
					'    downsampling: %d\n'%(self.acc_scales[i]) + \
					'    negative_ratio: 30\n' \
					'    circle_ratio: %f\n'%(self.circle_ratio) + \
					'    bounds_overlap: 0.33\n' + 
					'  }\n' \
					'}\n')

		return out


	def _layer_bb(self):
		"""
		Description of the MultiscaleAccumulatorLoss layer.
		"""
		out = '\n# ' + '-'*46 + ' BB ' + '-'*46 + ' #\n'

		for i in range(len(self.accs)):
			out += ('layer {\n' \
					'  # -----------------------  SCALE 1/%d  (FOV %d x %d)\n'%(self.acc_scales[i], self.last_in_scale_fov[self.acc_scales[i]], self.last_in_scale_fov[self.acc_scales[i]]) + \
					'  # -----------------------  Ideal bounding box size: %dx%d px\n'%(self.acc_bbs_ideal[i], self.acc_bbs_ideal[i]) + \
					'  name: "bb_x%d"\n'%(self.acc_scales[i]) + \
					'  type: "BB' + ('3' if self.bb_type == 'bb3txt' else '') + 'TXTBB"\n' \
					'  bottom: "' + self.accs[i] + '"\n'
					'  top: "' + self.accs[i] + '"\n' \
					'  bbtxt_bb_param {\n' \
					'    ideal_size: %f\n'%(self.acc_bbs_ideal[i]) + \
					'    downsampling: %d\n'%(self.acc_scales[i]) + \
					'  }\n' \
					'}\n')

		return out


	def _layer_input(self):
		"""
		Description of the input layer - for deployment.
		"""
		return ('layer {\n' \
				'  name: "data"\n' \
				'  type: "Input"\n' \
				'  top: "data"\n' \
				'  input_param { shape: { dim: 1 dim: 3 dim: 128 dim: 256 } }\n' \
				'}\n')


	def _layer_data(self, phase):
		"""
		Description of the data layer for train_val.

		Input:
			phase: string 'TRAIN' or 'TEST'
		"""
		out  = ('layer {\n' \
				'  name: "data"\n' \
				'  type: "BB' + ('3' if self.bb_type == 'bb3txt' else '') + 'TXTData"\n' \
				'  top: "data"\n' \
				'  top: "label"\n' \
				'  include {\n' \
				'    phase: ' + phase + '\n' \
				'  }\n' \
				'  image_data_param {\n' \
				'    source: ""\n' \
				'    batch_size: 16\n' \
				'  }\n' \
				'  bbtxt_param {\n' \
				'    width: 256\n' \
				'    height: 128\n' \
				'    reference_size_min: 60\n' \
				'    reference_size_max: 120\n' \
				'  }\n' \
				'}\n')

		return out



####################################################################################################
#                                               MAIN                                               # 
####################################################################################################

def check_path(path, is_folder=False):
	"""
	Checks if the given path exists.

	Input:
		path:      Path to be checked
		is_folder: True if the checked path is a folder
	Returns:
		True if the given path exists
	"""
	if not os.path.exists(path) or (not is_folder and not os.path.isfile(path)):
		print('ERROR: Path "%s" does not exist!'%(path))
		return False

	return True


def parse_arguments():
	"""
	Parse input options of the script
	"""
	parser = argparse.ArgumentParser(description='Generate train_val and deploy PROTOTXT files ' \
												 'of Caffe networks with multiscale accumulators.')

	parser.add_argument('path_config', metavar='path_config', type=str,
	                    help='A configuration TXT file with network structure')
	parser.add_argument('path_out', metavar='path_out', type=str,
	                    help='Path to the output folder')
	parser.add_argument('bb_type', metavar='bb_type', type=str,
	                    help='Type of data and loss layers. One of ["bbtxt", "bb3txt"]')

	args = parser.parse_args()

	if not check_path(args.path_config):
		parser.print_help()
		exit(1)
	if args.bb_type not in ['bbtxt', 'bb3txt']:
		print('ERROR: Incorrect data and loss type!')
		parser.print_help()
		exit(1)

	return args


def main():
	args = parse_arguments()
	
	ng = MACCNetGenerator(args.path_config, args.bb_type)
	ng.generate_prototxt_files(args.path_out)


if __name__ == '__main__':
    main()


