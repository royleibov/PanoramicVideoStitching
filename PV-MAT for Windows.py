'''
GUI for the panoramic video measurement and tracking app
'''

import threading
import gc
import tkinter as tk
import time
import re
import numpy as np
import cv2
import PIL
import PySimpleGUI as sg
from icon import icon_PVMAT
from fractions import Fraction
from PIL import ImageTk, Image, ImageDraw
from Stitcher import Stitcher
from typing import Callable, List, Tuple, Union


SCALE = min(sg.Window.get_screen_size()[0] / 1440.0, sg.Window.get_screen_size()[1] / 900.0)


class GUI:
    '''
    The working GUI using PySimpleGUI
    '''
    def __init__(self):
        '''
        Setup the GUI window and control its behavior
        '''
        # Setting up the GUI
        self.window = make_window1()
        self.window.bind('<Shift_L>', '+SHIFT DOWN+')
        self.window.bind('<Shift_R>', '+SHIFT DOWN+')
        self.show_help = True

        # To be set after 2nd window is loaded
        self.graph: sg.Graph = None
        self.slider = None
        self.counter = None
        self.play_pause = None
        self.magnify = None
        self.text: sg.Text = None

        self.progressbar = None

        # GUI variables
        self.play = False
        self.delay = 0.03 # For smooth video capturing IN SECONDS
        self.graph_width = self.graph_height = None # Set later
        self.num_frames = None # Total number of frames in video
        self.current_frame_num = 1
        self.magnify_id = None # ID of image in magnify object
        self.cross_id = None # ID of cross in magnify object
        self.magnify_width = self.magnify_height = None # Set later
        self.magnify_square_id = None # ID of magnifying square size visualizaion
        self.cursor = 'arrow'

        # Measurements
        self.distance_units = 'px' # Units of distance
        self.dragging = False # True if there's a drag on the graph
        self.start_point = self.end_point = None # Items start and end
        self.lastxy = None # To calculate delta of movement
        self.last_figure = None # Last draw figure ID
        self.calibration_ratio = -1 # Known distance in units / pixel distance
        self.Lines = {} # Save all the line objects: self.Lines[line_id] = [(x1,y1), (x2, y2), length_in_pixels]
        self.calibrating = False # Bool to tell if in calibration mode
        self.pixel_dist = -1 # line's pixel distance
        self.EdgeCircles = {} # Circles at edge of line to denote line selection. self.EdgeCircles[circle_id] = Circle. Circle = {'line_id': line_id, 'position': center_xy}
        self.distance_text_id = {}
        self.distance_text_background = {}
        self.draw_distance_text = False
        self.line_color = 'white'

        # Stitcher object and variables
        self.stitcher = Stitcher(self.window)
        self.panorama = None
        self.frame_locations = [] # Tuples of (frame, its corresponding location)
        self.PIL_pano = None
        self.image = None # ID of image on graph
        self.max_match_num = self.stitcher.max_match_num
        self.min_match_num = self.stitcher.min_match_num
        self.f = self.stitcher.f

        # Tracker variables
        self.fps = None
        self.tracker = cv2.TrackerCSRT_create()
        self.bounding_boxes = []
        self.draw_bounding_boxes = False
        self.bounding_id = None
        self.pano_width = 0
        self.pano_height = 0
        self.path_id = None
        self.COM_points = []
        self.COM_path = []
        self.draw_path = False
        self.velocities = [] # In pixels/second
        self.velocity_text_id = None
        self.velocity_background = None
        self.draw_velocity = False
        self.vel_units_ratio = -1 # Turn self.calibration_ratio/second to self.velocity_units
        self.velocity_units = 'km/h'
        self.path_color = 'white'



        while True: # GUI event loop
            event, values = self.window.read(timeout=self.delay*1000)

            # Handle exit or window closed
            if event in ("Exit", None, "-EXIT-"):
                break # Fall back to exit handeling
            # print(event, values)

            # Entered expert mode
            if event == '-EXPERT-':
                expert_mode_window(self.stitcher, self.min_match_num, self.max_match_num, self.f)

                # Save the settings for later only if the resolution wasn't played with
                if self.stitcher.get_resize_factor() == 1:
                    self.max_match_num = self.stitcher.max_match_num
                    self.min_match_num = self.stitcher.min_match_num
                    self.f = self.stitcher.f
        
            # Handle help
            if event == '-HELP-':
                # Stop the video from playing
                if self.play_pause:
                    self.play = False
                    self.play_pause.update("Play")

                help_window()

            # Show or hide Expert Mode by pressing SHIFT
            if event == '+SHIFT DOWN+':
                self.show_help = not self.show_help
                self.window['-HELP-'].update(visible=self.show_help)
                self.window['-EXPERT-'].update(visible=not self.show_help)
                self.window['-SIZER-'].set_size(size=(6,1) if self.show_help else (7,1))

            # Pick file and set variables by it
            if event == "-FILEPATH-":
                # If file path was given
                if values[event]:
                    # Perform the lond operation (threading) and catch the return of the panorama
                    self.window.perform_long_operation(lambda : self.stitcher.stitch(values[event]),
                                                        '-STITCHER DONE-')

                    # Make the progress bar
                    self.progressbar = make_progressbar()
                    self.progressbar.move_to_center()


            if event == '-UPDATE PROGRESS BAR-':
                subprocess_percents = [0, 75, 2, 23] # Percentages. Add up to 100
                subprocess_num, current_iter, sub_total_iter, text = values[event]

                # Linear map current_iter to percentage value out of subprocess[subprocess_num]
                percent = sum(subprocess_percents[:subprocess_num]) + \
                            int(((current_iter / sub_total_iter) * subprocess_percents[subprocess_num]))

                # Update progressbar
                self.progressbar['-PROGRESS BAR-'].update(percent)
                self.progressbar['-PERCENT-'].update(f'{percent}%')
                self.progressbar['-TEXT-'].update(text)

                self.progressbar.refresh()

            # Tracking progress meter
            if event == '-TRACKING PROGRESS-':
                sg.one_line_progress_meter('Tracking...', values[event] + 1, self.num_frames, orientation='h', no_button=True)


            if event == '-STITCHER DONE-':
                # Deal with error in stitching
                if values[event] is None:
                    self.progressbar.DisableClose = False
                    continue

                self.panorama = values[event]

                self.fps = self.stitcher.get_fps()
                assert self.fps, "Problem reading video." # shouldn't happen
                success_dump, frame_dump = self.stitcher.get_frame_dump()

                if success_dump:
                    # Fit frames to panorama
                    self.window.perform_long_operation(lambda : self.stitcher.locate_frames(self.panorama, frame_dump), '-LOCATOR DONE-')
                else:
                    raise AssertionError()


            if event == '-LOCATOR DONE-':
                # Deal with error in locating frames
                if values[event] is None:
                    self.progressbar.DisableClose = False
                    continue

                self.frame_locations = values[event] # (frame, location of edges [unnecessary])

                # Kill progress bar
                self.progressbar.close()
                del self.progressbar
                self.progressbar = None

                # Switch window to app functionality window
                self.window.close()
                del self.window
                self.window = make_window2()

                # Start threading to load video correctly
                self.thread(self.update)

                self.window.maximize()
                self.window.refresh()

                # define elements and attach key bindings
                self.graph = self.window['-GRAPH-']
                self.graph.bind('<Motion>', '+MOVE+')
                self.graph.bind('<ButtonRelease-1>', '+UP+')
                self.graph.bind('<ButtonPress-1>', '+DOWN+')

                self.slider = self.window['-SLIDER-']
                self.counter = self.window['-COUNTER-']
                self.play_pause = self.window['-PLAY-']

                self.magnify = self.window['-MAGNIFY-']
                self.text = self.window['-TEXT-']
                self.text.current_background = 'red'
                #self.window['-PLAY-'].set_focus()

                self.window.bind('<Left>', '-P FRAME-')
                self.window.bind('<Right>', '-N FRAME-')
                self.window.bind('<space>', '-PLAY-')

                self.window['-MAGNIFY SIZE-'].bind('<ButtonRelease-1>', '+UP+')
                self.window['-MAGNIFY SIZE-'].bind('<ButtonPress-1>', '+DOWN+')

                self.graph_width, self.graph_height = get_element_size(self.graph)
                self.magnify_width, self.magnify_height = get_element_size(self.magnify)

                # Display the panorama
                self.pano_height, self.pano_width = self.panorama.shape[:2]
                # print(self.pano_width, self.pano_height)

                # Set new graph size
                self.graph_width = self.window.size[0]
                self.graph_height = int(self.graph_width * self.pano_height / self.pano_width)
                if self.graph_height > self.window.size[1] * 38/100:
                    self.graph_height = self.window.size[1] * 38//100
                    self.graph_width = int(self.graph_height * self.pano_width / self.pano_height)

                self.graph.set_size((self.graph_width, self.graph_height))
                self.graph.change_coordinates(graph_bottom_left = (0, self.graph_height), graph_top_right = (self.graph_width, 0))

                # Draw panorama
                self.PIL_pano = self.draw_image(self.panorama)

                # Update the slider
                self.num_frames = len(self.frame_locations)
                self.slider.update(range=(1, self.num_frames))
                self.counter.update(f'1/{self.num_frames}')

                self.window.refresh()
    
                # self.window.move_to_center()


            # Video controls
            if event == '-P FRAME-':
                self.play = False
                self.play_pause.update("Play")

                # Wrap around
                if self.current_frame_num > 1:
                    self.current_frame_num = int(values['-SLIDER-'] - 1)
                else:
                    self.current_frame_num = self.num_frames

                # Draw frame
                self.image = self.goto_frame(self.current_frame_num, image_id=self.image) # Might be the first time

                # Send a graph event to update the magnifier
                self.window.write_event_value('-GRAPH-+MOVE+', None)

                #self.window['-PLAY-'].set_focus()

            if event == '-PLAY-':
                # self.play controls the update() function
                if self.play:
                    self.play = False
                    self.play_pause.update("Play")
                else:
                    self.play = True
                    self.play_pause.update("Pause")

            if event == '-N FRAME-':
                self.play = False
                self.play_pause.update("Play")

                # Wrap around
                if self.current_frame_num < self.num_frames:
                    self.current_frame_num = int(values['-SLIDER-'] + 1)
                else:
                    self.current_frame_num = 1

                self.image = self.goto_frame(self.current_frame_num, image_id=self.image) # Might be the first time

                # Send a graph event to update the magnifier
                self.window.write_event_value('-GRAPH-+MOVE+', None)

                #self.window['-PLAY-'].set_focus()

            if event == '-SLIDER-':
                self.play = False
                self.play_pause.update("Play")

                self.current_frame_num = int(values[event])
                self.image = self.goto_frame(self.current_frame_num, image_id=self.image) # Might be the first time

                # Send a graph event to update the magnifier
                self.window.write_event_value('-GRAPH-+MOVE+', None)


            if event == '-TRACK-':
                # Stop any video play
                self.play = False
                self.play_pause.update("Play")

                # Write to screen
                current_text = self.text.get() # Save for case of cancelling
                current_bg = self.text.current_background
                self.text.update('Entered tracking mode!', background_color='red')
                self.text.current_background = 'red'
                self.window.refresh()

                # Show instructions
                make_tracking_window()

                # Prepare ROI selection window
                frame = self.frame_locations[0][0]
                frame = cv2.cvtColor(frame, cv2.COLOR_RGBA2RGB)
                # Taken from https://broutonlab.com/blog/opencv-object-tracking
                # Select the bounding box in the first frame
                WindowName = 'Select Region of Interest'
                cv2.namedWindow(WindowName)
                frame = cv2.resize(frame, (self.graph_width, self.graph_height))
                window_width, window_height = self.window.size
                move_height = (window_height - self.graph_height) // 2
                move_width = (window_width - self.graph_width) // 2
                cv2.setWindowProperty(WindowName, cv2.WND_PROP_TOPMOST, 1)
                cv2.moveWindow(WindowName, move_width, move_height)

                bbox = cv2.selectROI(WindowName, frame, True) # bbox: (top_left_x, top_left_y, box_width, box_height)

                # print(f'bbox: {bbox} 0')
                cv2.destroyAllWindows()

                # Canceled
                if bbox == (0, 0, 0, 0):
                    self.text.update(current_text, background_color=current_bg)
                    self.text.current_background = current_bg

                    #self.window['-PLAY-'].set_focus()
                    continue

                # Go back to original frame size
                frame = cv2.resize(frame, (self.pano_width, self.pano_height))
                
                # Convert to orginial coordinates
                bbox = tuple([int(p * self.pano_width / self.graph_width) for p in bbox])

                # Reset previous tracking session
                del self.tracker
                self.tracker = cv2.TrackerCSRT_create()
                del self.bounding_boxes
                self.bounding_boxes = []
                self.draw_bounding_boxes = False
                del self.COM_points
                self.COM_points = []
                del self.COM_path
                self.COM_path = []
                del self.velocities
                self.velocities = []
                gc.collect()

                self.tracker.init(frame, bbox)

                # Do the tracking in a different thread
                self.thread(self.track)

            # Changed path color
            if event == '-PATH COLOR-':
                self.path_color = values[event]

                self.draw_track()

            # Changed velocity units
            if event == '-VEL UNITS-':
                self.velocity_units = values[event]
                self.set_velocity_units()

                #self.window['-PLAY-'].set_focus()

            # Tracker done. Draw bounding boxes and path
            if event == '-TRACKER DONE-':
                self.window['-BOX-'].update(True, disabled=False)
                self.draw_bounding_boxes = True

                self.window['-PATH-'].update(True, disabled=False)
                self.draw_path = True

                self.window['-PATH COLOR-'].update(disabled=False)

                self.draw_track()

                del self.tracker
                self.tracker = cv2.TrackerCSRT_create()
                gc.collect()

                # Calculate velocities if a calibration was already done
                if self.calibration_ratio > 0:
                    self.thread(self.calculate_velocity)

                    # Enable velocity features
                    self.window['-VELOCITY-'].update(True, disabled=False)
                    self.draw_velocity = True
                    self.window['-VEL UNITS-'].update(disabled=False)
                    self.window['-VEL UNITS-'].set_tooltip('Velocity Units')

                    # Calculate velocities given the units
                    self.set_velocity_units()

                    # Update Help Text
                    self.text.update('Velocity was added!', background_color=sg.theme_text_element_background_color())
                    self.text.current_background = sg.theme_text_element_background_color()
                else: # Suggest calibrating
                    self.text.update('Calibrate Distance to add velocity information.', background_color=sg.theme_text_element_background_color())
                    self.text.current_background = sg.theme_text_element_background_color()

                # If there was tracking failure, inform the user
                if -1 in self.COM_points:
                    help_text = self.text.get()

                    fail_index = self.COM_points.index(-1)
                    help_text += f' Tracking failure at frame {fail_index+1}.'

                    self.text.update(help_text)

                #self.window['-PLAY-'].set_focus()


            # Called if play button was pressed
            if event == '-THREAD UPDATE-':
                if self.image: # Not sure what's he use of the if statement
                    self.image = self.draw_image(values[event], image_id=self.image)
                    self.update_counter()

            # A change in drawing bounding box
            if event == '-BOX-':
                # Set to True
                if values[event]:
                    self.draw_bounding_boxes = True
                else:
                    self.draw_bounding_boxes = False
                    if self.bounding_id:
                        self.graph.delete_figure(self.bounding_id)
                        self.bounding_id = None
                self.draw_track()

            # A change in drawing COM path
            if event == '-PATH-':
                # Set to True
                if values[event]:
                    self.draw_path = True
                else:
                    self.draw_path = False
                    if self.path_id:
                        self.graph.delete_figure(self.path_id)
                        self.path_id = None
                self.draw_track()

            if event == '-VELOCITY-':
                # Set to True
                if values[event]:
                    self.draw_velocity = True
                else:
                    self.draw_velocity = False
                    if self.velocity_text_id:
                        self.graph.delete_figure(self.velocity_text_id)
                        self.velocity_text_id = None

                        self.graph.delete_figure(self.velocity_background)
                        self.velocity_background = None
                self.draw_track()


            # Switch between feet and inches and meters
            if event == '-DIS UNITS-':
                # Switch from feet and inches to meters
                if values[event] == 'Meters' and self.distance_units != 'Meters':
                    # Recalibrate
                    inches_to_meters_ratio = 1 / 39.37 # meters / inches
                    self.calibration_ratio *= inches_to_meters_ratio

                    # Set units
                    self.distance_units = 'Meters'
                    self.set_velocity_units()

                    # Change GUI
                    self.text.update('Select a line to see the difference.')
                    self.window['-LINE-'].reset_group()
                    self.graph.set_cursor('arrow')

                # Switch from meters to feet and inches
                if values[event] == 'Feet and Inches' and self.distance_units != 'Feet and Inches':
                    # Recalibrate
                    meters_to_inches_ratio = 39.37 # inches / meters
                    self.calibration_ratio *= meters_to_inches_ratio

                    # Set units
                    self.distance_units = 'Feet and Inches'
                    self.set_velocity_units()

                    # Change GUI
                    self.text.update('Select a line to see the difference.')
                    self.window['-LINE-'].reset_group()
                    self.graph.set_cursor('arrow')

                #self.window['-PLAY-'].set_focus()

                # Select the last line if exists to show the change of units
                if self.last_figure:
                    self.window['-SELECT-'].update(True)
                    values['-SELECT-'] = True

                    self.select_and_move(self.last_figure, (0,0))

                    self.cursor = 'fleur'
                    self.graph.set_cursor(self.cursor)
                    self.text.update('Select the line to drag or resize.')

                # Draw or delete other texts
                self.window.write_event_value('-SHOW DIS-', values['-SHOW DIS-'])

            
            # Changed distance line color
            if event == '-DIS COLOR-':
                self.line_color = values[event]

            # Changed distance line color
            if event == '-DIS COLOR-':
                self.line_color = values[event]


            # There's a change of magnify size
            if event == '-MAGNIFY SIZE-' or event == '-MAGNIFY SIZE-+DOWN+':
                if self.magnify_square_id:
                    self.graph.delete_figure(self.magnify_square_id)

                center_x, center_y = int(self.graph_width / 2), int(self.graph_height / 2)

                half_magnifier_size = int(values['-MAGNIFY SIZE-'] / 2)

                # Upper left x and y
                x0, y0 = center_x - half_magnifier_size, center_y + half_magnifier_size

                self.magnify_square_id = self.graph.draw_rectangle(top_left=(x0, y0),
                                                        bottom_right=(x0 + values['-MAGNIFY SIZE-'], y0 - values['-MAGNIFY SIZE-']),
                                                        line_color='white', line_width=2)

                # Draw the sample region on the magnifier
                self.window.write_event_value('-GRAPH-', (center_x, center_y))

            # Mouse lifted from slider
            if event == '-MAGNIFY SIZE-+UP+':
                self.graph.delete_figure(self.magnify_square_id)


            if event == '-SHOW DIS-':
                self.draw_distance_text = values[event]

                lines = self.Lines.copy()
                if self.last_figure and values['-SELECT-']:
                    lines.pop(self.last_figure)

                for line_id in lines:
                    self.update_distance(line_id, self.draw_distance_text)


            # Draw magnified area into Magnify graph.
            # Actually triggered most of the time with '+MOVE+'
            if event.startswith('-GRAPH-'):
                # print('Moving!')
                try:
                    # Set the magnifying square window: (magnify_size, magnify_size)
                    magnify_size = values['-MAGNIFY SIZE-']
                except KeyError:
                    continue

                x, y = values['-GRAPH-']
                if None in (x, y):
                    continue

                # Get upper left and lower right corners
                x1, y1 = x - int(magnify_size / 2), y - int(magnify_size / 2)
                x2, y2 = x1 + magnify_size, y1 + magnify_size

                # Current displayed frame id is saved in either self.image or self.PIL_pano
                image_id = self.image if self.image else self.PIL_pano

                try:
                    # Get all images currently displayed
                    frame = PIL.ImageTk.getimage(self.graph.Images[image_id])
                    pano = PIL.ImageTk.getimage(self.graph.Images[self.PIL_pano])
                    frame = PIL.Image.alpha_composite(pano, frame)

                    # Setup drawing the lines abd circles
                    draw = PIL.ImageDraw.Draw(frame)

                    for propreties in self.Lines.values():
                        draw.line(propreties[:2], fill=self.line_color, width=1)

                    # Check if hovering above or selecting a circle
                    on_circle = False
                    for circle_id in self.EdgeCircles:
                        # Gets circle's bounding boxes
                        top_left, bottom_right = self.graph.get_bounding_box(circle_id)

                        # Check if hovering above or selecting a circle
                        if (top_left[0] < x < bottom_right[0] and top_left[1] < y < bottom_right[1]):
                            on_circle = True
                        elif self.cursor == 'double_arrow' and self.dragging:
                            on_circle = True

                        # (x, y) points of the circle
                        x0_c, y0_c = top_left
                        x1_c, y1_c = bottom_right

                        bounding_width = abs(x1_c - x0_c)
                        bounding_height = abs(y1_c - y0_c)

                        shrink_percent = 75
                        shrink_factor = (shrink_percent / 100) / 2

                        # Change size of circle as displayed in magnifier area
                        x0_c += round(bounding_width * shrink_factor)
                        x1_c -= round(bounding_width * shrink_factor)
                        y0_c += round(bounding_height * shrink_factor)
                        y1_c -= round(bounding_height * shrink_factor)

                        # Draw the circle
                        draw.ellipse([x0_c, y0_c, x1_c, y1_c], fill='#2d65a4', outline='white')

                    # Change cursor if on a circle
                    if on_circle and self.cursor != 'double_arrow':
                        self.cursor = 'double_arrow'
                        self.graph.set_cursor(self.cursor)
                    elif not on_circle and values['-SELECT-'] and self.cursor != 'fleur':
                        self.cursor = 'fleur'
                        self.graph.set_cursor(self.cursor)

                    # Crop magnify bounding box
                    frame = frame.crop((x1, y1, x2, y2))
                    frame = PIL.ImageTk.PhotoImage(image=frame.resize((self.magnify_width, self.magnify_height), PIL.Image.Resampling.NEAREST))

                    # Draw on magnify area
                    self.magnify_id = self.draw_on_canvas(frame, self.magnify, self.magnify_id)
                    self.cross_id = self.draw_cross(self.magnify, self.cross_id)

                except IndexError:
                    print("ERROR")
                    # No image is diplayed in main graph



            # Finished drawing or dragging
            if event == '-GRAPH-+UP+':
                # print('up')
                # Handle calibration
                if self.calibrating and self.pixel_dist > 0:
                    self.calibrating = False

                    distance, distance_units = popup_get_distance()

                    # If distance was given
                    if distance > 0:
                        self.calibration_ratio = distance / self.pixel_dist # Either in inches/px or meters/px

                        if distance_units == 'Meters':
                            self.window['-DIS UNITS-'].update('Meters')
                        else:
                            self.window['-DIS UNITS-'].update('Feet and Inches')
                        self.distance_units = distance_units

                        self.window['-CALIB-'].update(button_color=('white', '#283b5b'))
                        self.update_distance(self.last_figure, True)

                        self.enable_toolbox()

                        # Calculate velocities when a calibration is set and a tracking was made
                        if len(self.COM_points) > 0:
                            self.thread(self.calculate_velocity)

                            self.window['-VELOCITY-'].update(True, disabled=False)
                            self.draw_velocity = True
                            self.window['-VEL UNITS-'].update(disabled=False)
                            self.window['-VEL UNITS-'].set_tooltip('Velocity Units')

                            self.set_velocity_units()

                            self.text.update('Velocity was added!', background_color=sg.theme_text_element_background_color())
                            self.text.current_background = sg.theme_text_element_background_color()
                        else:
                            self.text.update('Track Object to add velocity information.', background_color=sg.theme_text_element_background_color())
                            self.text.current_background = sg.theme_text_element_background_color()

                    else: # Cancled without giving a distance. Exit calibration mode
                        # Delete the line
                        if self.last_figure:
                            self.Lines.pop(self.last_figure)
                            self.graph.delete_figure(self.last_figure)

                            if self.last_figure in self.distance_text_id:
                                self.graph.delete_figure(self.distance_text_id[self.last_figure])
                                self.distance_text_id.pop(self.last_figure)

                                self.graph.delete_figure(self.distance_text_background[self.last_figure])
                                self.distance_text_background.pop(self.last_figure)
                            
                            self.last_figure = None

                        # Haven't previously set a calibration
                        if self.calibration_ratio < 0:
                            self.window['-LINE-'].update(False, disabled=True)
                            self.window['-SELECT-'].update(False, disabled=True)
                            self.window['-DIS COLOR-'].update(disabled=True)

                            self.cursor = 'arrow'
                            self.graph.set_cursor(self.cursor)

                            self.text.update('Please calibrate the ruler by dragging a line of a known distance.')
                        else:
                            self.enable_toolbox()

                            self.cursor = 'cross'
                            self.graph.set_cursor(self.cursor)

                            self.text.update('Please make a measurement by dragging a line!', background_color=sg.theme_text_element_background_color())
                            self.text.current_background = sg.theme_text_element_background_color()

                            self.distance_units = values['-DIS UNITS-']

                        self.window['-CALIB-'].update(button_color=('white', '#283b5b'))

                    #self.window['-PLAY-'].set_focus()

                # Reset everything
                self.start_point = self.end_point = None
                self.dragging = False
                # self.last_figure = None
                self.pixel_dist = -1


            # There was an event on the graph. Main drawing handeling
            if event.startswith('-GRAPH-'):
                # Don't allow to draw if there wasn't a calibration yet
                if not self.calibrating and self.calibration_ratio < 0:
                    continue

                x, y = values['-GRAPH-']

                # Pressed down on graph. Not in the middle of moving or creating a line.
                if not self.dragging and event.endswith('+DOWN+'):
                    self.start_point = (x, y)
                    self.dragging = True
                    self.lastxy = x, y
                    figures = self.graph.get_figures_at_location((x, y))
                    self.last_figure = None
                else: # Dragging = True, drawing or moving something
                    self.end_point = (x, y)

                # Only if a draw or drag was initiated
                if None not in (self.start_point, self.end_point):

                    delta_x, delta_y = x - self.lastxy[0], y - self.lastxy[1]
                    self.lastxy = x, y

                    # Draw a line
                    if values['-LINE-']:
                        # Calc length of line
                        # https://stackoverflow.com/questions/1401712/how-can-the-euclidean-distance-be-calculated-with-numpy
                        self.pixel_dist = np.linalg.norm(np.array(self.start_point) - np.array(self.end_point))

                        # Save space
                        if self.last_figure:
                            self.Lines.pop(self.last_figure)
                            self.graph.delete_figure(self.last_figure)

                            if self.last_figure in self.distance_text_id:
                                self.graph.delete_figure(self.distance_text_id[self.last_figure])
                                self.distance_text_id.pop(self.last_figure)

                                self.graph.delete_figure(self.distance_text_background[self.last_figure])
                                self.distance_text_background.pop(self.last_figure)

                        for line_id in self.Lines:
                            self.update_distance(line_id, self.draw_distance_text)

                        self.last_figure = self.graph.draw_line(self.start_point, self.end_point, color=self.line_color, width=2)

                        self.Lines[self.last_figure] = [self.start_point, self.end_point, self.pixel_dist]

                        self.update_distance(self.last_figure, True)

                    # Drag item
                    if values['-SELECT-']:
                        # print('drag')

                        for fig in figures:
                            # Handle resizing of line
                            if fig in self.EdgeCircles:
                                # Get the selection circles and determine which is dragged
                                my_circle = self.EdgeCircles.pop(fig)
                                other_id, other_circle = list(self.EdgeCircles.items())[0]

                                # Move the circle
                                self.graph.move_figure(fig, delta_x, delta_y)
                                my_circle['position'] = tuple(map(lambda a, b: a + b,
                                                                    my_circle['position'], (delta_x, delta_y)))

                                # Recalculate length of line
                                # https://stackoverflow.com/questions/1401712/how-can-the-euclidean-distance-be-calculated-with-numpy
                                self.pixel_dist = np.linalg.norm(np.array(my_circle['position']) - np.array(other_circle['position']))

                                # Working with line:
                                self.last_figure = my_circle['line_id']
                                # Save space
                                if self.last_figure:
                                    self.Lines.pop(self.last_figure)
                                    self.graph.delete_figure(self.last_figure)

                                    if self.last_figure in self.distance_text_id:
                                        self.graph.delete_figure(self.distance_text_id[self.last_figure])
                                        self.distance_text_id.pop(self.last_figure)

                                        self.graph.delete_figure(self.distance_text_background[self.last_figure])
                                        self.distance_text_background.pop(self.last_figure)

                                # Redraw line
                                self.last_figure = self.graph.draw_line(my_circle['position'], other_circle['position'], color=self.line_color, width=2)
                                self.graph.bring_figure_to_front(fig)
                                self.graph.bring_figure_to_front(other_id)

                                # Update line id
                                my_circle['line_id'] = self.last_figure
                                other_circle['line_id'] = self.last_figure

                                # Save line and resave circles
                                self.Lines[self.last_figure] = [my_circle['position'], other_circle['position'], self.pixel_dist]
                                self.EdgeCircles[fig] = my_circle
                                self.EdgeCircles[other_id] = other_circle

                                # Update text
                                self.update_distance(self.last_figure, True)

                            # Moving just the line or selecting it
                            elif fig in self.Lines:
                                if fig not in self.distance_text_id:
                                    lines = self.Lines.copy()
                                    lines.pop(fig)

                                    for line_id in lines:
                                        self.update_distance(line_id, self.draw_distance_text)

                                self.last_figure = fig

                                self.graph.move_figure(fig, delta_x, delta_y)

                                # Move the saved locations
                                both_points = [tuple(map(lambda a, b: a + b, pt, (delta_x, delta_y))) for pt in self.Lines[fig][:2]]

                                # Set new position
                                self.Lines[fig][:2] = both_points

                                # Select and move the line if already selected, update the text
                                self.select_and_move(fig, (delta_x, delta_y))
                                self.pixel_dist = self.Lines[fig][2] # Important DON'T REMOVE. (No idea why (maybe for calibrating by line selection))


                    # Erase item
                    if values['-ERASE-']:
                        # print('erase')

                        for fig in figures:
                            if fig in self.Lines:
                                # print(fig)
                                # print(self.Lines[fig])
                                self.Lines.pop(fig)
                                self.graph.delete_figure(fig)

                                if fig in self.distance_text_id:
                                    self.graph.delete_figure(self.distance_text_id[fig])
                                    self.distance_text_id.pop(fig)

                                    self.graph.delete_figure(self.distance_text_background[fig])
                                    self.distance_text_background.pop(fig)

                                if fig == self.last_figure:
                                    self.last_figure = None

                    # Erase all items
                    if values['-CLEAR-']:
                        # print('clear')
                        self.graph.erase()
                        del self.Lines
                        self.Lines = {}
                        self.PIL_pano = self.draw_image(self.panorama)
                        self.image = self.goto_frame(self.current_frame_num)
                        self.draw_track()
                        self.last_figure = None
                        del self.distance_text_id
                        self.distance_text_id = {}
                        del self.distance_text_background
                        self.distance_text_background = {}


            # Calibration button was pressed
            if event == '-CALIB-':
                # Not currently in calibration mode
                if not self.calibrating:
                    self.play = False
                    if self.last_figure in self.distance_text_id:
                        self.update_distance(self.last_figure, self.draw_distance_text)

                    self.calibrating = True
                    self.window['-CALIB-'].update(button_color=('#283b5b', 'white'))
                    self.text.update('Entered calibration mode! Drag or Select a line of a known distance.', background_color='red')
                    self.text.current_background = 'red'
                    self.calibration_toolbox()
                    self.cursor = 'cross'
                    self.graph.set_cursor(self.cursor)
                    self.distance_units = 'px'
                else:
                    # Exit calibration mode without calibrating. Not first time calibrating
                    if self.calibration_ratio > 0:
                        self.enable_toolbox()
                        self.calibrating = False
                        self.window['-CALIB-'].update(button_color=('white', '#283b5b'))
                        self.text.update('Please make a measurement by dragging a line!', background_color=sg.theme_text_element_background_color())
                        self.text.current_background = sg.theme_text_element_background_color()
                        self.distance_units = values['-DIS UNITS-']
                    else: # No calibration was ever done
                        self.calibrating = False
                        self.window['-CALIB-'].update(button_color=('white', '#283b5b'))
                        self.text.update('Please Track Object or Calibrate Distance.', background_color='red')
                        self.text.current_background = 'red'
                        self.window['-LINE-'].update(False, disabled=True)


            # Change cursor by toolbar selection
            if event == '-LINE-':
                if self.last_figure in self.distance_text_id:
                    self.update_distance(self.last_figure, self.draw_distance_text)

                self.cursor = 'cross'
                self.graph.set_cursor(self.cursor)
                self.text.update('Draw a line by dragging on the image.')
            
            if event == '-SELECT-':
                if self.last_figure and not self.calibrating:
                    self.select_and_move(self.last_figure, (0,0))
                elif self.last_figure in self.distance_text_id:
                    self.graph.delete_figure(self.distance_text_id[self.last_figure])
                    self.distance_text_id.pop(self.last_figure)

                    self.graph.delete_figure(self.distance_text_background[self.last_figure])
                    self.distance_text_background.pop(self.last_figure)

                self.cursor = 'fleur'
                self.graph.set_cursor(self.cursor)
                self.text.update('Select the line to drag or resize.')
            
            if event == '-ERASE-':
                if self.last_figure in self.distance_text_id:
                    self.update_distance(self.last_figure, self.draw_distance_text)
                    
                self.cursor = 'X_cursor'
                self.graph.set_cursor(self.cursor)
                self.text.update('Select a line to erase.')
            
            if event == '-CLEAR-':
                if self.last_figure in self.distance_text_id:
                    self.update_distance(self.last_figure, self.draw_distance_text)
                    
                self.cursor = 'iron_cross'
                self.graph.set_cursor(self.cursor)
                self.text.update('Press anywhere on the image to delete all the lines.')



            # Handle deleting the line selection circles.
            # I don't have a more clever solution at the moment |:
            if len(self.EdgeCircles) > 0:
                # values['-SELECT-'] == False
                if not values['-SELECT-']:
                    for circle_id in self.EdgeCircles.keys():
                        self.graph.delete_figure(circle_id)

                    del self.EdgeCircles
                    self.EdgeCircles = {}
                elif not self.last_figure:
                    self.last_figure = list(self.EdgeCircles.values())[0]['line_id']

            if event == 'Back':
                # Setup the starting window
                self.window.close()
                del self.window
                self.window = make_window1()
                self.window.bind('<Shift_L>', '+SHIFT DOWN+')
                self.window.bind('<Shift_R>', '+SHIFT DOWN+')
                self.show_help = True

                del self.stitcher
                self.stitcher = Stitcher(self.window, self.max_match_num, self.f)

                # Reset the stitcher
                self.stitcher.set_min_match_num(self.min_match_num)
                self.stitcher.set_f(self.f)
                del self.frame_locations
                self.frame_locations =[]

                # Reset the working app
                self.PIL_pano = None
                self.image = None
                self.current_frame_num = 1
                self.magnify_id = None
                self.cross_id = None
                self.magnify_square_id = None # ID of magnifying square size visualizaion
                self.cursor = 'arrow'
                self.distance_units = 'px' # Units of distance
                self.dragging = False # True if there's a drag on the graph
                self.start_point = self.end_point = None # Items start and end
                self.lastxy = None # To calculate delta of movement
                self.last_figure = None # Last draw figure ID
                self.calibration_ratio = -1 # Known distance in units / pixel distance
                del self.Lines
                self.Lines = {} # Save all the line objects: self.Lines[line_id] = [(x1,y1), (x2, y2)]
                self.calibrating = False # Bool to tell if in calibration mode
                self.pixel_dist = -1 # line's pixel distance
                del self.EdgeCircles
                self.EdgeCircles = {} # Circles at edge of line to denote line selection. self.EdgeCircles[circle_id] = Circle. Circle = {'line_id': line_id, 'position': center_xy}
                self.distance_text_id = {}
                self.distance_text_background = {}
                self.draw_distance_text = False
                self.line_color = 'white'
                self.play_pause = None

                self.stitcher.reset_stitcher()

                # Reset tracker
                del self.tracker
                self.tracker = cv2.TrackerCSRT_create()
                del self.bounding_boxes
                self.bounding_boxes = []
                self.draw_bounding_boxes = False
                self.bounding_id = None
                self.pano_height = self.pano_width = 0
                self.path_id = None
                del self.COM_points
                self.COM_points = []
                self.draw_path = False
                del self.velocities
                self.velocities = []
                self.velocity_text_id = None
                self.velocity_background = None
                self.draw_velocity = False
                self.vel_units_ratio = -1
                self.velocity_units = 'km/h'
                del self.COM_path
                self.COM_path = []
                self.path_color = 'white'

                gc.collect()
                

        self.window.close()
        del self.window



    # --- GUI visual updates --- #

    def goto_frame(self, frame_num, image_id: int = None) -> Union[int, None]:
        '''
        Set video to specific frame. Might be first time drawing so returns image_id.
        '''
        if len(self.frame_locations) > 0:
            self.update_counter(frame_num)

            frame, _ = self.frame_locations[frame_num-1]

            return self.draw_image(frame, image_id=image_id)

    def update_counter(self, frame_num: int = None):
        '''
        Update the slider's counter.
        MAKE SURE TO UPDATE self.current_frame_num BEFORE CALLING.
        '''

        frame_num = frame_num if frame_num else self.current_frame_num

        self.counter.update(f'{frame_num}/{self.num_frames}')
        self.slider.update(value=frame_num)
        self.slider.set_tooltip(f'{frame_num}')

        # Update the track drawing (if exists) only on frame change or settings change
        self.draw_track()

    def update_distance(self, line_id: int, draw_text: bool):
        '''
        Update the distance text based on the given distance.
        '''
        if line_id in self.distance_text_id:
            self.graph.delete_figure(self.distance_text_id[line_id])
            self.distance_text_id.pop(line_id)

            self.graph.delete_figure(self.distance_text_background[line_id])
            self.distance_text_background.pop(line_id)

        if not draw_text:
            return

        distance_from_line = 15

        a, b, pixel_distance = self.Lines[line_id]

        measurement = pixel_distance * self.calibration_ratio if not self.calibrating else pixel_distance
        measurement_text = convert_to_units(measurement, self.distance_units)

        a = np.array(a)
        b = np.array(b)
        ab = b - a
        mid_point = (a + b) / 2

        perpendicular_slope = -ab[0]/ab[1] if ab[1] != 0 else 1
        right_perpendicular = np.array([1.0, perpendicular_slope])
        right_perpendicular /= np.linalg.norm(right_perpendicular)

        text_location = sg.TEXT_LOCATION_RIGHT

        if right_perpendicular[1] < 0:
            right_perpendicular = -right_perpendicular
            text_location = sg.TEXT_LOCATION_LEFT

        text_pt = (mid_point - distance_from_line * right_perpendicular).tolist()

        self.distance_text_id[line_id] = self.graph.draw_text(measurement_text, text_pt, color='white',
                                                            font='_ 18', text_location=text_location)
        top_left, bottom_right = self.graph.get_bounding_box(self.distance_text_id[line_id])
        self.distance_text_background[line_id] = self.graph.draw_rectangle(top_left, bottom_right, fill_color='#000000')
        self.graph.bring_figure_to_front(self.distance_text_id[line_id])

    def enable_toolbox(self):
        '''
        Enable all the radio buttons.
        '''
        self.window['-LINE-'].update(True, disabled=False)
        self.window['-SELECT-'].update(disabled=False)
        self.window['-ERASE-'].update(disabled=False)
        self.window['-CLEAR-'].update(disabled=False)
        self.window['-DIS UNITS-'].update(disabled=False)
        self.window['-SHOW DIS-'].update(disabled=False)
        self.window['-DIS COLOR-'].update(disabled=False)

    def calibration_toolbox(self):
        '''
        Disable all radio  buttons except the line.
        '''
        self.window['-LINE-'].update(True, disabled=False)
        self.window['-SELECT-'].update(disabled=False)
        self.window['-ERASE-'].update(disabled=True)
        self.window['-CLEAR-'].update(disabled=True)
        self.window['-DIS COLOR-'].update(disabled=False)

    def set_velocity_units(self):
        '''
        Set the velocity units ratio depending on the wanted units and the distance units.
        '''
        # Do nothing if either no calibration was made or no tracking was done
        if self.calibration_ratio < 0 or len(self.bounding_boxes) == 0:
            return

        meters_per_second_to_velocity_units = {'m/s': 1, 'km/h': 3.6, 'ft/s': 3.281, 'mph': 2.237}
        inches_per_second_to_velocity_units = {'m/s': 0.0254, 'km/h': 0.09144, 'ft/s': 0.0833, 'mph': 0.05682}

        if self.distance_units == 'Meters':
            self.vel_units_ratio = meters_per_second_to_velocity_units[self.velocity_units]
        elif self.distance_units == 'Feet and Inches':
            self.vel_units_ratio = inches_per_second_to_velocity_units[self.velocity_units]

        self.draw_track()


    def select_and_move(self, line_id: int, delta: Tuple[int, int]):
        '''
        Select the line by drawing circles at its edges, printing its size, and moving or resizing it if already selected.
        '''

        circle_radius = 3

        # Check if the circles belong to selected line
        if line_id in [circle['line_id'] for circle in self.EdgeCircles.values()]:
            for circle_id, circle in self.EdgeCircles.items():
                self.graph.bring_figure_to_front(circle_id)
                self.graph.move_figure(circle_id, delta[0], delta[1])
                circle['position'] = tuple(map(lambda a, b: a + b, circle['position'], delta))
        else: # Line isn't selected, so select it.
            # Delete circles that belond to other lines
            if len(self.EdgeCircles) > 0:
                for circle_id, circle in self.EdgeCircles.items():
                    self.graph.delete_figure(circle_id)
                    self.graph.delete_figure(circle_id)
                self.EdgeCircles = {}

            # Create new edge circles
            circle1 = {'line_id': line_id}
            circle2 = {'line_id': line_id}

            # Aquire the line's edges. point = (x, y)
            point_one, point_two = self.Lines[line_id][:2]

            circle1['position'] = point_one
            circle2['position'] = point_two

            # Draw circles on graph
            circle1_id = self.graph.draw_circle(point_one, circle_radius,fill_color='#2d65a4', line_color='white')
            circle2_id = self.graph.draw_circle(point_two, circle_radius, fill_color='#2d65a4', line_color='white')

            # Save circles
            self.EdgeCircles[circle1_id] = circle1
            self.EdgeCircles[circle2_id] = circle2
            # print(self.EdgeCircles)

        self.update_distance(line_id, True)



    # --- Threading --- #

    def thread(self, threaded_func: Callable):
        '''
        Run a threaded function, given by threaded_func.
        '''
        thread = threading.Thread(target=threaded_func, args=())
        thread.daemon = True
        thread.start()

    def update(self):
        '''
        Do the actual heavy lifting of the video capturing proccess. Play the video (hopefully) smoothly.
        '''
        while True:
            start_time = time.time()
            if len(self.frame_locations) > 0 and self.play:
                if self.current_frame_num < self.num_frames:

                    frame, _ = self.frame_locations[self.current_frame_num]

                    self.current_frame_num += 1

                    try:
                        # Update the image and the counter outside of the thread
                        self.window.write_event_value('-THREAD UPDATE-', frame)

                        # Send a graph event to update the magnifier
                        self.window.write_event_value('-GRAPH-+MOVE+', None)
                    except AttributeError:
                        return
                else:
                    self.current_frame_num = 1


            # Neccecary sleep time (depending on FPS) to ensure smooth playback
            delay = self.delay - (time.time() - start_time)
            if delay < 0:
                # print("WOW!")
                delay = 0
            # print(f"        update time: {delay}")
            time.sleep(delay)
        # self.update()


    def track(self):
        '''
        Track an already intitiated item on self.tracker.
        '''
        # Start tracking
        for i in range(self.num_frames):
            # Aquire frame
            frame = self.frame_locations[i][0]
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB)

            # Get next bounding box
            ret, bbox = self.tracker.update(frame)

            if ret:
                # Top left point of bbox in graph coordinates
                p1 = (int(bbox[0] * self.graph_width / self.pano_width),
                        int(bbox[1] * self.graph_height / self.pano_height))

                # Bottom right point of bbox in graph coordinates
                p2 = (int((bbox[0] + bbox[2]) * self.graph_width / self.pano_width),
                        int((bbox[1] + bbox[3]) * self.graph_height / self.pano_height))
                
                # Middile point of bbox
                mid_point = (int((p1[0] + p2[0]) / 2) , int((p1[1] + p2[1]) / 2))

                # print(f'Found ROI {i}: {p1}, {p2}')
                # print(f'bbox: {bbox} {i}')

                # Svae the points
                self.bounding_boxes.append((p1, p2))
                self.COM_points.append(mid_point)
                try:
                    self.COM_path.append(self.COM_path[i-1]+[mid_point])
                except IndexError:
                    self.COM_path.append([mid_point])

            else: # Couldn't find the tracked object
                # print(f'Tracking failure detected {i}')
                # print(f'bbox: {bbox} {i}')

                # Save filler points
                self.bounding_boxes.append((-1, -1))
                self.COM_points.append(-1)
                try:
                    self.COM_path.append(self.COM_path[i-1])
                except IndexError:
                    self.COM_path.append([])

            # Update progressbar
            self.window.write_event_value('-TRACKING PROGRESS-', i)

        # Activate features after tracker is done
        self.window.write_event_value('-TRACKER DONE-', None)
        # print('Done!')


    def calculate_velocity(self):
        '''
        Calculate the object's velocity.
        '''
        # Reset velocities
        del self.velocities
        self.velocities = []
        gc.collect()

        num_mid_points = len(self.COM_points) # Total number of mid points
        assert num_mid_points > 0, "Called velocity calculation with no object tracked"
        assert self.calibration_ratio > 0, "Called velocity calculation with no distance calibration"

        seconds_per_frame = 1 / self.fps

        self.velocities.append(0.0)

        for i in range(1, num_mid_points - 1):
            # Aquire points
            before, current, after = self.COM_points[i-1], self.COM_points[i], self.COM_points[i+1]

            if current == -1:
                self.velocities.append(0.0)
                continue

            before_delta = 1
            while before == -1:
                try:
                    before = self.COM_points[i-1-before_delta]
                    before_delta += 1
                except IndexError:
                    before = current

            after_delta = 1
            while after == -1:
                try:
                    after = self.COM_points[i+1+after_delta]
                    after_delta += 1
                except IndexError:
                    after = current
            
            # Calculate length of lines
            # https://stackoverflow.com/questions/1401712/how-can-the-euclidean-distance-be-calculated-with-numpy
            dist_before = np.linalg.norm(np.array(before) - np.array(current))
            dist_after = np.linalg.norm(np.array(current) - np.array(after))

            total_dist = dist_before + dist_after

            # Velocity at self.COM_points[i] by average of delta_t
            avg_velocity = total_dist / ((before_delta + after_delta) * seconds_per_frame)

            self.velocities.append(avg_velocity)
        
        self.velocities.append(0.0)
        # print(self.velocities)


    # --- GUI draw functions --- #

    def draw_track(self):
        '''
        Draw bounding box, path, or velocity
        '''
        # Drawing the Center Of Mass (COM) path
        if self.draw_path:
            # Aquire all points of COM up until current frame. Exclude frames with tracking failure
            points = self.COM_path[self.current_frame_num - 1]

            # Clear any exsitent path previously drawn
            if self.path_id:
                self.graph.delete_figure(self.path_id)

            # Draw path and save its id
            self.path_id = self.graph.draw_lines(points, color = self.path_color, width=2)

        # Drawing the bounding boxes and velocities
        if len(self.bounding_boxes) >= self.current_frame_num:
            # Get current frame's bounding box
            top_left, bottom_right = self.bounding_boxes[self.current_frame_num - 1]

            # If there's a box on screen clear it
            if self.bounding_id:
                self.graph.delete_figure(self.bounding_id)

            # Delete previous text
            if self.velocity_text_id:
                self.graph.delete_figure(self.velocity_text_id)
                self.graph.delete_figure(self.velocity_background)

            # If frame's box was a tracking failure do nothing
            if -1 in (top_left, bottom_right):
                return

            # Draw bounding box if checked
            if self.draw_bounding_boxes:
                # Draw the box and save figure's id
                self.bounding_id = self.graph.draw_rectangle(top_left=top_left,
                                                        bottom_right=bottom_right,
                                                        line_color='red', line_width=2)

            # Draw velocities iff current frame's velocity exists AND draw velocities is checked
            if len(self.velocities) >= self.current_frame_num and self.draw_velocity:
                # Sanity checks. Shouldn't fire.
                assert self.calibration_ratio > 0, "Problem calculating velocities with no distance calibration"
                assert self.vel_units_ratio > 0, "Problem calculating velocities with no velocity units calibration"

                # print(f'In frame {self.current_frame_num}, vel in {self.distance_units}/sec: {self.velocities[self.current_frame_num - 1] * self.calibration_ratio}')
                # Convert px/sec to self.velocity_units units
                velocity = self.velocities[self.current_frame_num - 1] * self.calibration_ratio * self.vel_units_ratio
                # print(f'vel in {self.velocity_units}: {velocity}')

                # Print text to screen
                velocity_text = f'Vel: {velocity:.3} {self.velocity_units}'
                self.velocity_text_id = self.graph.draw_text(velocity_text, (top_left[0], top_left[1]-20), font='_ 20', color='white')
                top_left, bottom_right = self.graph.get_bounding_box(self.velocity_text_id)
                self.velocity_background = self.graph.draw_rectangle(top_left, bottom_right, fill_color='#000000')
                self.graph.bring_figure_to_front(self.velocity_text_id)


    def draw_image(self, frame, graph = None, image_id: int = None) -> Union[int, None]:
        """
        Draw the frame onto canvas element. VERY time efficient.
        Returns image_id if none given.
        """
        # frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
        graph = graph if graph else self.graph

        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)

        # Resize to fit graph element and create ImageTk object for fast loading
        frame = PIL.ImageTk.PhotoImage(
            image=PIL.Image.fromarray(frame, mode='RGBA').resize((self.graph_width, self.graph_height), PIL.Image.Resampling.NEAREST)
        )

        image_id = self.draw_on_canvas(frame, graph, image_id)

        return image_id # Could be None

    def draw_on_canvas(self, frame: PIL.ImageTk.PhotoImage, graph: sg.Graph, image_id: int = None) -> Union[int, None]:
        '''
        Actually draw frame on canvas quickly.
        Gets an image_id if available. Returns the id if none was given
        '''
        # First time loading a frame    
        if image_id is None:
            image_id = graph.tk_canvas.create_image((0, 0), image=frame, anchor=tk.NW)
            # we must mimic the way sg.Graph keeps track of its added objects:
            graph.Images[image_id] = frame
        else:
            # we reuse the image object, only changing its content
            graph.tk_canvas.itemconfig(image_id, image=frame)
            # we must update this reference too: 
            graph.Images[image_id] = frame

        return image_id
  
    def draw_cross(self, magnify: sg.Graph, cross_id: int = None) -> Union[int, None]:
        '''
        Draw a cross in the middle of magnify window. Get the magnify graph to draw on and the (optional) cross id.
        '''
        # Cross constants
        CROSS_HALF_WIDTH = 1
        HALF_HEIGHT = 10 + CROSS_HALF_WIDTH

        middle_x, middle_y = int(self.magnify_width / 2), int(self.magnify_height / 2)
        # print(middle_x, middle_y)

        points = get_cross_points((middle_x, middle_y), HH = HALF_HEIGHT, CHW = CROSS_HALF_WIDTH)

        if cross_id:
            magnify.bring_figure_to_front(cross_id)
        else:
            cross_id = magnify.draw_polygon(points, fill_color='yellow')
            return cross_id


# --- Methods --- #

def get_cross_points(middle: Tuple[int, int], HH: int, CHW: int) -> List[int]:
    '''
    Gets the points to draw a cross.
    middle = (x, y) for element's middle, HH = Half_Height for cross, CHW = Cross_Half_Width for cross.
    '''
    mx, my = middle

    p1 = (mx - CHW, my + HH) # Upper left
    p2 = (mx + CHW, my + HH) # Upper right
    p3 = (mx + CHW, my + CHW) # Middle right up
    p4 = (mx + HH, my + CHW) # Rightmost up
    p5 = (mx + HH, my - CHW) # Rightmost down
    p6 = (mx + CHW, my - CHW) # Middle right down
    p7 = (mx + CHW, my - HH) # Lowermost right
    p8 = (mx - CHW, my - HH) # Lowermost left
    p9 = (mx - CHW, my - CHW) # Middle left down
    p10 = (mx - HH, my - CHW) # Leftmost down
    p11 = (mx - HH, my + CHW) # Leftmost up
    p12 = (mx - CHW, my + CHW) # Middle left up

    return [p1, p2, p3, p4, p5, p6, p7, p8, p9, p10, p11, p12]

def get_element_size(element: sg.Element):
    '''
    Get the actual size of an element even if it hasn't been seen before.
    '''

    return element.Widget.winfo_reqwidth(), element.Widget.winfo_reqheight()

def help_window():
    '''
    Initalize the help window.
    '''
    global SCALE
    
    heading_font = '_ 20 bold underline'
    text_font = '_ 16'
    question_font = '_ 18'

    RIGHT_ARROW = '▶ '
    DOWN_ARROW = '▼ '

    def HelpText(text, visible=True, key=None) -> sg.Text:
        return sg.Text(text, size=(80, None), font=text_font, visible=visible, key=key)

    def QuestionText(text, key, font=question_font) -> sg.Text:
        return sg.T(text, key=key, font=font, pad=(0,10), enable_events=True)

    help_why = \
"""The name PV-MAT (Panoramic Video Measurement and Tracking) is an ironic play on "The Ideal Gas Law: PV=nRT" - There's nothing \
ideal about the chaotic movement of real-life objects.

Let's start with a review of the Goals of the PV-MAT project (the App):
1. To learn more and analyze anything that comes to mind
2. For you to be successful

This App solves a necessity that came up in training, but could be expanded to anything involving a video and some movement.

Given the restrictions put forth in the starting window (a video as short as possible and camera movement constrained to one axis \
- Up<->Down, Left<->Right, etc.) I believe this App can be very powerful!"""

    help_faq = \
"""If you have followed the restrictions and Help Text and are still experiencing problems try and find a solution here:"""

    q1_answer = \
"""There are a plethora of problems that can arise in the panorama-making step, but a few you can solve are:

1. Progressbar is stuck on some step of \"Stitching panorama's _ side (#)/(All #)\" - If the number in (All #) is sufficiently big \
(say 12 and above, but it's not an exact science) the video provided might have been too long for your computer to handle \
with the current way the algorithm is programmed. If not, you should consider learning to tinker with the stitching algorithm using Expert \
Mode (instructions below).

2. The frames are being processed REALLY slowly - Either the video is too massive, and you should consider learning to tinker \
with 'resize_factor' in Expert Mode (instructions below), or the focal_length (f) is too small for the given video, and you again need \
to tinker with it in Expert Mode.

3. The progressbar is stuck in some other phase - The program probably got stuck somewhere and takes long to load, I would consider \
closing out of everything (using the Exit button on the main window) and loading the video again."""

    q2_answer = \
"""Note that (if I'm being harsh on myself) even a perfect measurement is ±1% off.
With that in mind, there's probably a mistake in one or more of 3 points of failure:

1. The panorama was built incorrectly - I tried to write the most efficient code to build the panorama from just the \
video, with no information about the camera. That's A LOT of math and it's possible I made a mistake somewhere. But take \
into consideration that if the plane of interest (the plane in 3D space where measurements are to be made) changes as \
the object is moving, either towards or away from the camera, any calibration won't be sufficient. The App does not know \
how to deal well with depth.

2. There was a mistake in the calibration - Any 2-3 pixels mistake in the line drawn can be quite noticeable \
in the measurement. Do as best you can to place the calibration line correctly. You can even edit a drawn line and select it \
as the calibration line.

3. There was a mistake in the measurement line - Again, 2-3 pixels off could be a huge difference. Try to adjust it by utilizing \
the Magnifying Area (hint: you can zoom in by dragging the slider next to it).

As you can imagine, no one mistake happens alone most of the time, and those mistakes add up. So take all the \
measurements with a grain of salt."""

    q3_answer = \
"""I had the choice of either sacrificing space, both in disk and on memory, or speed and accuracy. I chose to cherish \
the space the App takes and to get by with less \"expensive\" tracking algorithms.

Though the tracking algorithm doesn't utilize deep learning, and so is less accurate, it is still reliable enough to deal \
with most tracking situations. Most of the time, all you need to do is persistently rechoose slightly different Regions of \
Interest (ROIs) until you arrive at a satisfactory result.

Tips and Tricks:
1. The better the video's resolution the easier it will be for the tracker to do its job. So consider working with videos with \
higher resolutions (that haven't gone through any compression in WhatsApp or the like). Also consider to lower the 'resize_factor' \
if you have tinkered with Expert Mode.

2. Choose high-contrast areas as ROIs. Try and select different areas of the object you are tracking and not just slightly \
readjust the rectangle. I found that for people, mainly those who are far away, tracking their head gives better results \
then tracking their entire body.

For the limitations of the tracking algorithm, see 'Points for Thought' below."""

    end_faq = \
"""If there are any other problems that arise while using the App please let me know. Any small unexpected behavior will be \
heavily looked on and exterminated so think long and hard before you take upon yourself the death of a bug."""

    help_expert_mode = \
"""You should invoke Expert Mode if you are familiar with panorama stitching or believe you are tech savvy enough \
to mentally deal with its bugs.
With that out of the way, to open Expert Mode all you need to do is press SHIFT while at the starting window and \
the 'Help' button should magically transform into a 'More' button. Press on it to invoke Expert Mode. Press SHIFT again to \
revert back to 'Help'.

If you weren't deterred by my attempt to frighten you and want to learn how to use Expert Mode (apart from the helpful tooltips \
found when hovering over the question marks (?)) here's the guide:

When the stitching algorithm chooses which frames in the video to stitch together, it does so in a way that minimizes \
the overlapping area between consecutive frames as much as possible. By comparing the matched area (in number of identical \
key-points between two frames), it chooses frames which fit between 'min_match_num' and 'max_match_num'.

min_match_num - Minimum number of matching key-points between the frames for a frame to be chosen for stitching. I would suggest NOT \
changing it and tinkering with 'max_match_num' instead. If you must change it, I would suggest NOT going below 20.

max_match_num - Maximum number of matching key-points between the frames for a frame to be chosen for stitching. The smaller this \
variable, the lesser the overlap between frames chosen for stitching, and the fewer frames will be stitched (which improves \
loading time).

focal length (f) - Put simply, the closer you are to the plane of interest the smaller f should be. Literally, it's the radius of \
the circle the camera sweeps when you pan across a scene, or a combination of it and the distance of the camera from the plane (in 3D) \
you're interested in. Practically, you can think of it as the measurement of how curved the frames in the video should be. \
The smaller f the curvier the frames are, and the closer you are to the plane of interest or bigger the angle the camera sweeps.

resize_factor - The number to divide the video's resolution by. Very useful in cases where you want to load a video fast to tinker \
with the other parameters before processing the full resolution video and actually measuring and tracking. Bear in mind that \
the video could process differently when in full resolution. I tried to compensate for changes but it's not a guarantee."""

    points_to_improve = \
"""There are still many areas where I can still put in more work and indeed I do plan on doing so in the future. In my eyes, \
this is Version 1 of the program and there's a lot of room for improvement:

1. The stitching algorithm - I spent a lot of time trying different methods before I landed with the current one in place. \
I learned "deep learning" and a good chunk of the math involved with it, I took apart the Stitching API of OpenCV \
and reimplemented it to better fit my specific need, and devoted considerable time into learning linear algebra - \
the main language of computer vision. Through it all I made sure to meaningfully understand the algorithms involved before \
committing them to my code.
I have put considerable effort into accelerating the code processing each frame, but it can be even faster with better utilization \
of the GPU's parallel processing capabilities.

2. The final panorama - The field of image stitching has evolved a lot in the past few years. The idea of taking a video detached \
from its source camera and inferring the intrinsic properties of that camera was extremely hard just 6-7 years ago.
Now we are at a time when such technologies pop up daily on our phones.
I can use more advanced stitching methods to ensure better looking, seamlessly stitched, panoramas.

3. Measurements - The whole experience of the App is supposed to imitate, in my eyes, the way editing programs (e.g. Photoshop, \
PowerPoint, etc.) look and feel.
I can put in more work into making that experience a reality. Right now there are known (and unknown) bugs or missing \
features, like multiple lines moving together when selected in a certain way, the lack of ability to select more than one line at \
a time, and many more.

4. Tracking - The tracking is admittedly the aspect of the project to which I devoted the least amount of time. Right now tracking \
and object detection are \"hot\" fields in machine learning, with many new, bigger, and better algorithms coming out monthly.
I could have chosen a more accurate or even faster algorithm for tracking but chose to stay with the one I view as best among \
OpenCV's catalog. Improving the tracking experience can greatly improve the usefulness of my program.

5. User experience - I believe a great deal that a good, concise, and understandable UI will lead to good UX.
I can add in the future the ability to opt-out of waiting all the way through while stitching the panorama or tracking \
an object. Right now trying to close the progressbars does nothing, because I found it too difficult at the moment to implement \
a stopping mechanism on seperate threads.

Lastly, the experience of working with PySimpleGUI, a beautifully built, maintained, and documented library for GUI making, \
was exquisite. Dipping my feet in the world of program-making proved to be not as daunting as I thought thanks to \
PySimpleGUI community's guidence. I'm sure I will build many more programs and projects thanks to this smooth experience."""
    layout = [
                [sg.T('Goals', font=heading_font, pad=(0,10))],
                [HelpText(help_why)],
                [sg.HorizontalSeparator(p=20)],
                [sg.T('FAQ', font=heading_font, pad=(0,10))],
                [HelpText(help_faq)],
                [QuestionText(RIGHT_ARROW, '-Q1-'),
                 QuestionText('Why does the panorama take ages to process and why need the video be as short as possible?', '-Q1-TEXT')],
                [sg.pin(HelpText(q1_answer, visible=False, key='-A1-'))],
                [QuestionText(RIGHT_ARROW, '-Q2-'),
                 QuestionText('My distance measurements seem a bit off...', '-Q2-TEXT')],
                [sg.pin(HelpText(q2_answer, visible=False, key='-A2-'))],
                [QuestionText(RIGHT_ARROW, '-Q3-'),
                 QuestionText('Why doesn\'t the Object Tracker work properly?', '-Q3-TEXT')],
                [sg.pin(HelpText(q3_answer, visible=False, key='-A3-'))],
                [HelpText(end_faq)],
                [sg.HorizontalSeparator(p=20)],
                [QuestionText(RIGHT_ARROW, key='-Q4-', font=heading_font[:-10]), # Key to utilize collapsable feature
                 sg.T('Expert Mode (optional)', font=heading_font, pad=(0,10), key='-Q4-TEXT', enable_events=True)], # Key to utilize collapsable feature
                [sg.pin(HelpText(help_expert_mode, visible=False, key='-A4-'))], # Key to utilize collapsable feature
                [sg.HorizontalSeparator(p=20)],
                [QuestionText(RIGHT_ARROW, key='-Q5-', font=heading_font[:-10]), # Key to utilize collapsable feature
                 sg.T('Points for Thought', font=heading_font, pad=(0,10), key='-Q5-TEXT', enable_events=True)],
                [sg.pin(HelpText(points_to_improve, visible=False, key='-A5-'))]
              ]
    window = sg.Window('GUI Help', [[sg.Push(), sg.Col(layout, scrollable=True, vertical_scroll_only=True, expand_y=True, k='-COL-'), sg.Push()],
                                    [sg.B('Close', font='_ 14')]],
                            keep_on_top=True, finalize=True, resizable=True, scaling=SCALE, icon=icon_PVMAT)
    window.size = (window.size[0], sg.Window.get_screen_size()[1])
    window.move(int((sg.Window.get_screen_size()[0] - window.size[0]) / 2), 0)

    while True:
        event, _ = window.read()
         
        if event in ('Close', None):
            break
        
        if event.startswith('-Q'):
            question_number = re.split('-|Q', event)[2]
            arrow = window['-Q'+question_number+'-'].get()

            if arrow == RIGHT_ARROW:
                window['-Q'+question_number+'-'].update(DOWN_ARROW)
                window['-A'+question_number+'-'].update(visible=True)
            else:
                window['-Q'+question_number+'-'].update(RIGHT_ARROW)
                window['-A'+question_number+'-'].update(visible=False)
            window.refresh()
            window['-COL-'].contents_changed()

    window.close()
    del window

def expert_mode_window(stitcher: Stitcher, min_num: int, max_num:int, f: int):
    '''
    A window to chagne the stitcher's parameters.
    '''
    col = [[sg.Text('min_match_num', size=(15,1), font='_ 14'), sg.Input(default_text=f'{min_num}', size=(8,1), k='-MIN-', enable_events=True, font='_ 14'),
               sg.Text('?', font='_ 14', size=(1,1), tooltip='Controls the minimum amount of overlap between\nstitched frames of the panorama.\nThe smaller min_match_num is, the lesser the overlap.\nIT\'S BETTER TO TINKER WITH max_match_num!')],
              [sg.Text('max_match_num', size=(15,1), font='_ 14'), sg.Input(default_text=f'{max_num}', size=(8,1), k='-MAX-', enable_events=True, font='_ 14'),
               sg.Text('?', font='_ 14', size=(1,1), tooltip='Controls the maximum amount of overlap between\nstitched frames of the panorama.\nThe larger max_match_num is, the greater the overlap.')],
              [sg.Text('focal length (f)', size=(15,1), font='_ 14'), sg.Input(default_text=f'{f}', size=(8,1), k='-F-', enable_events=True, font='_ 14'),
               sg.Text('?', font='_ 14', size=(1,1), tooltip='Controls the focal point of the panorama.\nThe smaller f, the greater the curvature of the panorama.\nRule of Thumb: The greater the angle the camera pans,\nthe smaller f should be, and vice versa.')],
              [sg.Text('resize_factor', size=(15,1), font='_ 14'), sg.Spin([1,2,3,4,5], initial_value=stitcher.get_resize_factor(), size=(4,1), k='-RESIZE-', enable_events=True, readonly=True, background_color='white', font='_ 14'),
               sg.Text('?', font='_ 14', size=(1,1), tooltip='Controls the amount by which the quality decreases.\nBest for debuging and perfecting the\nappropriate parameters for a given video.')],
              [sg.B('Save', font='_ 14')]]

    layout = [[sg.Text(expand_x=True, expand_y=True, font='ANY 1', pad=(0, 0))],  # the thing that expands from top
              [sg.Column(col, element_justification='center' ,vertical_alignment='center', justification='center', expand_x=True, expand_y=True)]]

    window = sg.Window('Expert Mode', layout, text_justification='c', finalize=True, resizable=True, modal=True, scaling=1.0, icon=icon_PVMAT)

    values = None

    while True:
        event, values = window.read()

        if event in ('Save', None):
            break

        if event in ('-MIN-', '-MAX-', '-F-'):
            try:
                int(values[event])
            except ValueError:
                window[event].update(values[event][:-1])


    if values is not None:
        stitcher.set_min_match_num(int(values['-MIN-']))
        stitcher.set_max_match_num(int(values['-MAX-']))
        stitcher.set_f(int(values['-F-']))
        if values['-RESIZE-'] != stitcher.get_resize_factor():
            stitcher.set_resize_factor(int(values['-RESIZE-']))

    window.close()
    del window

def make_progressbar() -> sg.Window:
    '''
    Make the progressbar popup.
    '''
    global SCALE
    
    layout = [[sg.ProgressBar(100, key='-PROGRESS BAR-', size=(100, 20)), sg.Text('0%', k='-PERCENT-', font=('Helvetica', 16))],
                [sg.Push(), sg.Text('Loading...', key='-TEXT-', font=('Helvetica', 16)), sg.Push()]]

    return sg.Window("Making the panorama...", layout, finalize=True, disable_close=True, keep_on_top=True, scaling=SCALE, icon=icon_PVMAT)

def make_tracking_window():
    '''
    Make the tracking explaination window popup.
    '''
    global SCALE
    
    layout = [[sg.Text('Track Object', font='_ 18')],
              [sg.Text('Press the \'Next\' button to select an object by dragging a rectangle on the image.', font='_ 16')],
              [sg.Text('In the next window, to begin tracking press the ENTER or SPACE keys on your keyboard.', font='_ 16')],
              [sg.Text('In the next window, to cancel the tracking press the \'C\' letter key on your keyboard.', font='_ 16')],
              [sg.B('Next', font='_ 16', k='-NEXT-')]]

    window = sg.Window("Track Object", layout, finalize=True, disable_close=True, keep_on_top=True, element_justification='c',
                       scaling=SCALE, text_justification='c', modal=True, icon=icon_PVMAT)
    window['-NEXT-'].set_focus(True)
    window.bind('<Return>', '-NEXT-')

    while window.read()[0] not in ('Next', None, '-NEXT-'):
        continue
    window.close()
    del window

def make_window1() -> sg.Window:
    '''
    Make window1 of browsing.
    '''
    global SCALE
    
    col = [[sg.Text(s=(6,1), k='-SIZER-'), sg.Push(), sg.Text('Select video', font='_ 18'), sg.Push(),
            sg.vtop(sg.pin(sg.B('More', font='_ 16', visible=False, k='-EXPERT-'))),
            sg.vtop(sg.pin(sg.B('Help', font='_ 16', k='-HELP-')))
           ],
           [sg.Text(font='_ 16', text='Please upload as short a video as you can (I would suggest 6-10 seconds max).')],
           [sg.Text('Also pretty please, make sure the video is shot with movement on a single axis (or else the algorithm freaks).', font='_ 16')],
           [sg.In(key="-FILEPATH-", enable_events=True, font='_ 16'),
            sg.FileBrowse("Browse", font='_ 16', file_types=(('All Video Files', '*.mp4 *.mov *.mkv *.avi *.wmv'),))],
           [sg.B('Exit', k='-EXIT-', font='_ 16')]]

    layout = [[sg.Text(expand_x=True, expand_y=True, font='ANY 1', pad=(0, 0))],  # the thing that expands from top
              [sg.Column(col, element_justification='center' ,vertical_alignment='center', justification='center', expand_x=True, expand_y=True)]]

    return sg.Window("PV-MAT - Panoramic Video Measurement and Tracking", layout=layout, finalize=True, scaling=SCALE, resizable=True,
                     text_justification='c', icon=icon_PVMAT)


def make_window2() -> sg.Window:
    '''
    Make window2 of actual app functionality.
    '''
    global SCALE
    
    distance_toolbar = [[sg.VPush()],
           [sg.R('Draw Line', 1, k='-LINE-', enable_events=True, font=('Helvetica', 16), disabled=True)],
           [sg.R('Select and Edit Line', 1, k='-SELECT-', enable_events=True, font=('Helvetica', 16), disabled=True)],
           [sg.R('Erase Item', 1, k='-ERASE-', enable_events=True, font=('Helvetica', 16), disabled=True)],
           [sg.R('Erase All', 1, k='-CLEAR-', enable_events=True, font=('Helvetica', 16), disabled=True)],
           [sg.VPush()],
           [sg.Checkbox('Show All Distances', disabled=True, k='-SHOW DIS-', enable_events=True, font='_ 16')],
           [sg.VPush()],
           [sg.Combo(['Meters', 'Feet and Inches'], default_value='Meters', readonly=True, k='-DIS UNITS-',
                        enable_events=True, tooltip='Distance Units', font='_ 16', disabled=True)],
           [sg.VPush()],
           [sg.Text('Line Color:', font='_ 16'), sg.Combo(['white', 'black', 'red', 'green', 'blue'],
                        default_value='white', readonly=True, k='-DIS COLOR-',
                        enable_events=True, tooltip='Line Color', font='_ 16', disabled=True)],
           [sg.VPush()]]
    
    track_and_calibrate = [[sg.Text(expand_x=True, expand_y=True, font='_ 16', pad=(0,0))],
                        [sg.B('Track Object', k='-TRACK-', font='_ 16')],
                        [sg.B('Calibrate Distance', k='-CALIB-', font='_ 16')],
                                 [sg.VPush()]]

    tracking_toolbar = [[sg.VPush()],
                [sg.Check('Draw Path', k='-PATH-', enable_events=True, disabled=True, font='_ 16')],
                [sg.Check('Draw Bounding Box', k='-BOX-', enable_events=True, disabled=True, font='_ 16')],
                [sg.VPush()],
                [sg.Check('Show Velocity', disabled=True, k='-VELOCITY-', enable_events=True, font='_ 16')],
                [sg.Combo(['m/s', 'km/h', 'ft/s', 'mph'], default_value='km/h', k='-VEL UNITS-',
                        enable_events=True, disabled=True, readonly=True,
                        tooltip='Velocity Units\nOnly available after\ndistance calibration and object tracking', font='_ 16')],
                [sg.VPush()],
                [sg.Text('Path Color:', font='_ 14'), sg.Combo(['white', 'black', 'red', 'green', 'blue'], default_value='white', readonly=True, k='-PATH COLOR-',
                        enable_events=True, tooltip='Path Color', font='_ 16', disabled=True)],
                [sg.VPush()]]

    magnifier = [[sg.Slider(range=(100, 10), default_value=40, resolution=10, orientation='v',
                    enable_events=True, k='-MAGNIFY SIZE-', tooltip='Magnifying Area'),
                  sg.Graph((200,200), graph_bottom_left=(0,200), key='-MAGNIFY-',
                    graph_top_right=(200,0), background_color="light grey")
                ]]

    layout = [[sg.vtop(sg.B('Help', font='_ 14', k='-HELP-')), sg.Push(),
               sg.Frame('Help Text',
                        [[sg.Text('Please Track Object or Calibrate Distance.', font='_ 16',
                            k='-TEXT-', background_color='red', justification='c', expand_x=True)]],
                            font='_ 14', title_location=sg.TITLE_LOCATION_TOP, expand_y=True, expand_x=True,
                            element_justification='c'),
               sg.Push()],
               [sg.VPush()],
              [
                sg.Push(), sg.Graph((720,480), graph_bottom_left=(0,1080), key='-GRAPH-',
                            graph_top_right=(1920,0), background_color=sg.theme_text_element_background_color(),
                            motion_events=True, enable_events=True, drag_submits=True),
                sg.Push()
              ],
              [sg.Push(), sg.Frame('Playback Controls',
                    [[sg.B("Previous Frame", key="-P FRAME-", tooltip='[LEFT KEY]\n<-', font='_ 14'),
                    sg.B("Play", key="-PLAY-", tooltip='[SPACE]', font='_ 14'),
                    sg.B("Next Frame", key="-N FRAME-", tooltip='[RIGHT KEY]\n->', font='_ 14'),
                    sg.Slider(range=(0, 100), k="-SLIDER-", orientation="h",
                                enable_events=True, expand_x=True, disable_number_display=True, tooltip='1'),
                    sg.Text("0/0", k="-COUNTER-", font='_ 14')]], font='_ 14', title_location=sg.TITLE_LOCATION_TOP,
                    expand_x=True, expand_y=True, element_justification='c'),
                sg.Push()
              ],
              [sg.VPush()],
              [sg.Push(),
               sg.Frame('Track & Calibrate', track_and_calibrate, font='_ 16', element_justification='center',
                                 vertical_alignment='center', expand_x=True, expand_y=True),
               sg.Push(),
               sg.Frame('Tracking Toolbar', tracking_toolbar, font='_ 16', expand_y=True, pad=(0,0)),
               sg.Frame('Distance Toolbar',distance_toolbar, font='_ 16', expand_y=True, pad=(0,0)),
               sg.Push(),
               sg.Frame('Magnifier', magnifier, font='_ 16'),
               sg.Push()],
              [sg.B('Back', font='_ 14'), sg.Push(), sg.B('Exit', font='_ 14')]]

    return sg.Window("Panoramic Video Measurement and Tracking", layout=layout, finalize=True, scaling=SCALE, resizable=True, element_justification='c', icon=icon_PVMAT)

def popup_get_distance() -> Tuple[Union[float, None], str]:
    '''
    Get the distance given by the user.
    '''    
    value = -1
    distance_units = ''

    layout = [[sg.Text('Please provide the distance of the drawn line as you know it', auto_size_text=True, font='_ 16')],
               [sg.InputText(key='-IN-', enable_events=True, default_text='0[.0]', text_color='gray', focus=False, font='_ 16'),
                sg.Combo(['Meters', 'Feet and Inches'], default_value='Meters', k='-UNITS-', enable_events=True, readonly=True, font='_ 16')
              ],
               [sg.Button('Ok', size=(6, 1), bind_return_key=True, focus=True, font='_ 16'), sg.Button('Cancel', size=(6, 1), bind_return_key=True, font='_ 16')]]

    window = sg.Window(title='Get Distance', layout=layout,  auto_size_text=True, keep_on_top=True, finalize=True, modal=True, scaling=1.0, icon=icon_PVMAT)
    window['-IN-'].bind("<FocusIn>", "+FOCUS IN+")
    window['-IN-'].bind("<FocusOut>", "+FOCUS OUT+")
    window['-IN-'].bind("<Button-1>", "+CLICK+")

    while True:
        event, values = window.read()

        if event in ("Cancel", None):
            value = -1
            break

        # if last character in input element is invalid, remove it
        if event == '-IN-' and values['-IN-']:
            # Entering text instead of placeholder text
            if values['-IN-'][:-1] == "0'[0 [0/0]\"]" or values['-IN-'][:-1] == '0[.0]':
                window['-IN-'].update(value=values['-IN-'][-1], text_color='black')

            if values['-UNITS-'] == 'Meters':
                try:
                    float(values['-IN-'])
                except ValueError:
                    window['-IN-'].update(values['-IN-'][:-1])
            else: # values['-UNITS-'] == 'Feet and Inches'
                # Can enter non pattern conforming values. Pattern checked at submittion.
                if values['-IN-'][-1] not in ('0123456789\'" /'):
                    window['-IN-'].update(values['-IN-'][:-1])


        # Example of acceptable distance entries
        if event == '-UNITS-':
            # Changed to feet and inches
            if values[event] == 'Feet and Inches':
                window['-IN-'].update(value="0'[0 [0/0]\"]", text_color='gray')
            # Changed to meters
            elif values[event] == 'Meters':
                window['-IN-'].update(value='0[.0]', text_color='gray')

        # Focused on input
        if event == '-IN-+FOCUS IN+' or event == '-IN-+CLICK+':
            # Clean the input field when first starting to write
            if values['-IN-'] == "0'[0 [0/0]\"]" or values['-IN-'] == '0[.0]':
                window['-IN-'].update(value='', text_color='black')

        # Not writing and exiting focus
        if event == '-IN-+FOCUS OUT+':
            # If nothing was written, put in placeholder text.
            if values['-IN-'] == '':
                window.write_event_value('-UNITS-', values['-UNITS-'])

        # Submit button
        if event == 'Ok':
            distance = values['-IN-']
            distance_units = values['-UNITS-']
            if distance_units == 'Feet and Inches':
                try:
                    # Check if input conforms with 0'[0 [0/0]"] format
                    match = re.match(r"^(?:(\d+)')?(?: *(\d+)?(?: +(?!0+\/)(\d+\/(?!0+\")\d+))?\")?$", values['-IN-'])

                    feet = int(match.group(1)) if match.group(1) else 0
                    inches = int(match.group(2)) if match.group(2) else 0
                    fraction_inch = float(Fraction(match.group(3))) if match.group(3) else 0

                    value = feet * 12 + inches + fraction_inch # In inches
                except AttributeError:
                    window['-IN-'].update('')
                    window.write_event_value('-IN-+FOCUS OUT+', None)
                    continue
            else: # Units == Meters
                try:
                    value = float(distance) # In meters
                except ValueError:
                    window['-IN-'].update('')
                    window.write_event_value('-IN-+FOCUS OUT+', None)
                    continue
            break

    window.close()
    del window

    return value, distance_units

def convert_to_units(measurement: Union[int, float], units: str) -> str:
    '''
    Convert distance in {units} to its units.
    '''
    measurement_text = ''
    if units == 'Feet and Inches':
        # Convert from [inches] to [feet'inches and fraction of an inch"]
        feet = int(measurement // 12)
        inches = int(measurement % 12)
        fraction_inch = Fraction(int(8 * ((measurement % 12) - inches)), 8) if measurement % 12 != 0 else 0

        measurement_text = f'{feet}\'{inches} {fraction_inch}"' if fraction_inch != 0 else f'{feet}\'{inches}"'
    else: # Meters or pixels
        measurement_text = f'{measurement: .2f} {units}'
    return measurement_text


if __name__ == '__main__':
    GUI()
