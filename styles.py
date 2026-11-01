# -*- coding: utf-8 -*-
# Qt style sheet for shack.py: app.setStyleSheet(styles.style)

style = '''
	QPushButton {
		border: 1px solid #808080;
		border-radius: 4px;
		background-color: #e8e8e8;
		color: #000000;
		border-bottom: 2px solid #000000;
		border-right: 2px solid #000000;
		padding-left: 1px;
		padding-right: 1px;
		padding-bottom: 1px;
	}

	QPushButton[text="Cancel"] {
		color: red;
	}

	QPushButton[text="OK"] {
		color: #008000;
	}

	QPushButton:disabled {
		color: #b0b0b0;
	}

	QPushButton:hover {
		color: #ff0000;
	}

	QPushButton:pressed {
		background-color: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 #dadbde, stop: 1 #f6f7fa);
	}

	QPushButton:hover:!pressed {
		border: 1px solid red;
	}

	QPushButton:checked {		/* switch on: green, pushed in (heavy edge moves to top-left) */
		background-color: #2a7d2a;
		color: #ffffff;
		border: 1px solid #808080;
		border-top: 2px solid #000000;
		border-left: 2px solid #000000;
	}

	QPushButton:checked:disabled {
		background-color: #a8c8a8;
		color: #e8e8e8;
	}

	QLabel:enabled, QSpinBox:enabled, QDoubleSpinBox:enabled, QComboBox:enabled {
		font-weight: bold;
	}

	QLabel:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
		color: #c8c8c8;
	}
'''

# shack.py's decoder area: the control row, the spectrum strip under it, the decoded text
decoder = dict(
	row='#dde6f0',		# background of the Decoder row
	text='#fbf8ec',		# background of the decoded text
	spectrum='#14202c',		# strip background
	fill='#4a9eff',		# the spectrum itself
	grid='#2c3a48',		# 500 Hz lines
	labels='#8a9aaa',		# Hz numbers
	squelch='#b0b0b0',		# dashed squelch line
)
