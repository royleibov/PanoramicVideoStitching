'''
GUI for the panoramic stitching app
'''

import threading
import sys
import tkinter as tk
import time
import glob
import re
import numpy as np
import cv2
import PIL
import PySimpleGUI as sg
from fractions import Fraction
from PIL import ImageTk, Image, ImageDraw
from Stitcher import Stitcher
from typing import Callable, List, Tuple, Union
    

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

        # To be set after 2nd window is loaded
        self.graph: sg.Graph = None
        self.slider = None
        self.counter = None
        self.play_pause = None
        self.magnify = None
        self.text = None

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
        self.magnify_square_id = None # ID of magnifing square size visualizaion
        self.cursor = 'arrow'

        # Measurments
        self.distance_units = 'px' # Units of distance
        self.dragging = False # True if there's a drag on the graph
        self.start_point = self.end_point = None # Items start and end
        self.lastxy = None # To calculate delta of movement
        self.last_figure = None # Last draw figure ID
        self.calibration_ratio = -1 # Known distance in units / pixel distance
        self.Lines = {} # Save all the line objects: self.Lines[line_id] = [(x1,y1), (x2, y2)]
        self.calibrating = False # Bool to tell if in calibration mode
        self.pixel_dist = -1 # line's pixel distance
        self.EdgeCircles = {} # Circles at edge of line to denote line selection. self.EdgeCircles[circle_id] = Circle. Circle = {'line_id': line_id, 'position': center_xy}

        # Stitcher object and variables
        self.stitcher = Stitcher(self.window)
        self.panorama = None
        self.frame_locations = [] # Tuples of (frame, its corresponding location)
        self.PIL_pano = None
        self.image = None # ID of image on graph
        self.max_match_num = self.stitcher.max_match_num
        self.min_match_num = self.stitcher.min_match_num
        self.f = self.stitcher.f

        # Start threading to load video correctly
        self.thread(self.update)



        while True: # GUI event loop
            event, values = self.window.read()

            # Handle exit or window closed
            if event in ("Exit", None, "-EXIT-"):
                break # Fall back to exit handeling
            # print(event, values)

            # Entered expert mode
            if event == 'More':
                expert_mode_window(self.stitcher, self.min_match_num, self.max_match_num, self.f)

                if self.stitcher.get_resize_factor() == 1:
                    self.max_match_num = self.stitcher.max_match_num
                    self.min_match_num = self.stitcher.min_match_num
                    self.f = self.stitcher.f
            
            # Handle help
            if event == 'Help':
                help_window()

            # Pick file and set variables by it
            if event == "-FILEPATH-":
                # If file path was given
                if values[event]:
                    # Perform the lond operation (threading) and catch the return of the panorama
                    self.window.perform_long_operation(lambda : self.stitcher.stitch(values[event]),
                                                        '-STITCHER DONE-')

                    # Make the progress bar
                    self.progressbar = make_progressbar()


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


            if event == '-STITCHER DONE-':
                # Deal with error in stitching
                if values[event] is None:
                    self.progressbar.DisableClose = False
                    continue

                self.panorama = values[event]

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

                self.window.bind('<Left>', '-P FRAME-')
                self.window.bind('<Right>', '-N FRAME-')
                self.window.bind('<space>', '-PLAY-')

                self.window['-MAGNIFY SIZE-'].bind('<ButtonRelease-1>', '+UP+')
                self.window['-MAGNIFY SIZE-'].bind('<ButtonPress-1>', '+DOWN+')

                self.graph_width, self.graph_height = get_element_size(self.graph)
                self.magnify_width, self.magnify_height = get_element_size(self.magnify)

                # Display the panorama
                pano_height, pano_width = self.panorama.shape[:2]
                print(pano_width, pano_height)

                # Set new graph size
                self.graph_width = 1280
                self.graph_height = int(self.graph_width * pano_height / pano_width)
                if self.graph_height > 480:
                    self.graph_height = 480
                    self.graph_width = int(self.graph_height * pano_width / pano_height)

                self.graph.set_size((self.graph_width, self.graph_height))
                self.graph.change_coordinates(graph_bottom_left = (0, self.graph_height), graph_top_right = (self.graph_width, 0))

                # Draw panorama
                self.PIL_pano = self.draw_image(self.panorama)

                # Update the slider
                self.num_frames = len(self.frame_locations)
                self.slider.update(range=(1, self.num_frames))
                self.counter.update(f'1/{self.num_frames}')

                self.window.refresh()
                
                self.window.move_to_center()


            # Video controls
            if event == '-P FRAME-':
                self.play = False
                self.play_pause.update("Play")

                if self.current_frame_num > 1:
                    self.current_frame_num = int(values['-SLIDER-'] - 1)
                else:
                    self.current_frame_num = self.num_frames

                self.image = self.goto_frame(self.current_frame_num, image_id=self.image) # Might be the first time

                # Send a graph event to update the magnifier
                self.window.write_event_value('-GRAPH-+MOVE+', None)

            if event == '-PLAY-':
                if self.play:
                    self.play = False
                    self.play_pause.update("Play")
                else:
                    self.play = True
                    self.play_pause.update("Pause")

            if event == '-N FRAME-':
                self.play = False
                self.play_pause.update("Play")

                if self.current_frame_num < self.num_frames:
                    self.current_frame_num = int(values['-SLIDER-'] + 1)
                else:
                    self.current_frame_num = 1

                self.image = self.goto_frame(self.current_frame_num, image_id=self.image) # Might be the first time

                # Send a graph event to update the magnifier
                self.window.write_event_value('-GRAPH-+MOVE+', None)

            if event == '-SLIDER-':
                self.play = False
                self.play_pause.update("Play")

                self.current_frame_num = int(values[event])
                self.image = self.goto_frame(self.current_frame_num, image_id=self.image) # Might be the first time

                # Send a graph event to update the magnifier
                self.window.write_event_value('-GRAPH-+MOVE+', None)



            # Called if play button was pressed
            if event == '-THREAD UPDATE-':
                self.image = self.draw_image(values[event], image_id=self.image)
                self.update_counter()


            # Switch from feet and inches to meters
            if event == '-METERS-':
                inches_to_meters_ration = 1 / 39.37 # meters / inches

                self.calibration_ratio *= inches_to_meters_ration

                self.distance_units = 'Meters'

                self.text.update('Select a line to see the difference.')

                self.window['-LINE-'].reset_group()

                self.graph.set_cursor('arrow')

            # Switch from meters to feet and inches
            if event == '-FEET-':
                meters_to_inches_ration = 39.37 # inches / meters

                self.calibration_ratio *= meters_to_inches_ration

                self.distance_units = 'Feet and Inches'

                self.text.update('Select a line to see the difference.')

                self.window['-LINE-'].reset_group()

                self.graph.set_cursor('arrow')



            # There's a change of magnify size
            if event == '-MAGNIFY SIZE-' or event == '-MAGNIFY SIZE-+DOWN+':
                if self.magnify_square_id:
                    self.graph.delete_figure(self.magnify_square_id)

                center_x, center_y = int(self.graph_width / 2), int(self.graph_height / 2)

                half_magnifier_size = int(values['-MAGNIFY SIZE-'] / 2)

                # Upper left eft x and y
                x0, y0 = center_x - half_magnifier_size, center_y + half_magnifier_size

                self.magnify_square_id = self.graph.draw_rectangle(top_left=(x0, y0),
                                                        bottom_right=(x0 + values['-MAGNIFY SIZE-'], y0 - values['-MAGNIFY SIZE-']),
                                                        line_color='white', line_width=2)

                # Draw the sample region on the magnifier
                self.window.write_event_value('-GRAPH-', (center_x, center_y))
            
            # Mouse lifted from slider
            if event == '-MAGNIFY SIZE-+UP+':
                self.graph.delete_figure(self.magnify_square_id)



            # Draw magnified area into Magnify graph.
            # Actually triggered most of the time with '+MOVE+'
            if event.startswith('-GRAPH-'):
                # print('Moving!')
                # Set the magnifing square window: (magnify_size, magnify_size)
                magnify_size = values['-MAGNIFY SIZE-']
 
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
                        draw.line(propreties[:2], fill='white', width=1)
                    
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
                        shrink_factor = (shrink_percent / 100) * 1/2

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
                    elif values['-SELECT-'] and self.cursor != 'fleur':
                        self.cursor = 'fleur'
                        self.graph.set_cursor(self.cursor)
                               
                    # Crop magnify bounding box
                    frame = frame.crop((x1, y1, x2, y2))
                    frame = PIL.ImageTk.PhotoImage(image=frame.resize((self.magnify_width, self.magnify_height), PIL.Image.NEAREST))

                    # Draw on magnify area
                    self.magnify_id = self.draw_on_canvas(frame, self.magnify, self.magnify_id)
                    self.cross_id = self.draw_cross(self.magnify, self.cross_id)
                
                except IndexError:
                    print("ERROR")
                    # No image is diplayed in main graph



            # Finished drawing or dragging
            if event == '-GRAPH-+UP+':
                print('up')
                # Handle calibration
                if self.calibrating and self.pixel_dist > 0:
                    distance, self.distance_units = popup_get_distance()

                    if self.distance_units == 'Meters':
                        self.window['-METERS-'].update(True)
                    else:
                        self.window['-FEET-'].update(True)

                    # If distance was given
                    if distance > 0:
                        self.calibration_ratio = distance / self.pixel_dist
                        self.window['-CALIB-'].update(button_color=('white', '#283b5b'))
                        self.text.update(f'Line set to {convert_to_units(distance, self.distance_units)}', text_color='white')
                        self.enable_toolbox()
                    else: # Cancled without giving a distance. Exit calibration mode
                        # Delete the line
                        if self.last_figure:
                            self.Lines.pop(self.last_figure)
                            self.graph.delete_figure(self.last_figure)

                        # Haven't previously set a calibration
                        if self.calibration_ratio < 0:
                            self.window['-LINE-'].update(False, disabled=True)
                            self.window['-SELECT-'].update(False, disabled=True)
                            self.cursor = 'arrow'
                            self.graph.set_cursor(self.cursor)
                            self.text.update('Please calibrate the ruler by dragging a line of a known distance.')
                        else:
                            self.enable_toolbox()
                            self.cursor = 'cross'
                            self.graph.set_cursor(self.cursor)
                            self.text.update('Please make a measurment by dragging a line!', text_color='white')

                        self.window['-CALIB-'].update(button_color=('white', '#283b5b'))

                    self.calibrating = False

                # Reset everything
                self.start_point = self.end_point = None
                self.dragging = False
                self.last_figure = None
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

                        self.update_distance(self.pixel_dist)

                        # Save space
                        if self.last_figure:
                            self.Lines.pop(self.last_figure)
                            self.graph.delete_figure(self.last_figure)
                        
                        self.last_figure = self.graph.draw_line(self.start_point, self.end_point, color='white', width=2)

                        self.Lines[self.last_figure] = [self.start_point, self.end_point, self.pixel_dist]

                    # Drag item
                    if values['-SELECT-']:
                        print('drag')

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

                                # Update text
                                self.update_distance(self.pixel_dist)

                                # Working with line:
                                self.last_figure = my_circle['line_id']
                                # Save space
                                if self.last_figure:
                                    self.Lines.pop(self.last_figure)
                                    self.graph.delete_figure(self.last_figure)
                                
                                # Redraw line
                                self.last_figure = self.graph.draw_line(my_circle['position'], other_circle['position'], color='white', width=2)
                                self.graph.bring_figure_to_front(fig)
                                self.graph.bring_figure_to_front(other_id)

                                # Update line id
                                my_circle['line_id'] = self.last_figure
                                other_circle['line_id'] = self.last_figure

                                # Save line and resave circles
                                self.Lines[self.last_figure] = [my_circle['position'], other_circle['position'], self.pixel_dist]
                                self.EdgeCircles[fig] = my_circle
                                self.EdgeCircles[other_id] = other_circle

                            # Moving just the line or selecting it
                            elif fig in self.Lines:
                                self.graph.move_figure(fig, delta_x, delta_y)

                                # Move the saved locations
                                both_points = [tuple(map(lambda a, b: a + b, pt, (delta_x, delta_y))) for pt in self.Lines[fig][:2]]

                                # Set new position
                                self.Lines[fig][:2] = both_points

                                # Select and move the line if already selected, update the text
                                self.select_and_move(fig, (delta_x, delta_y))
                                self.pixel_dist = self.Lines[fig][2] # Important DON'T REMOVE. (No idea why)

                    # Erase item
                    if values['-ERASE-']:
                        print('erase')

                        for fig in figures:
                            if fig not in (self.PIL_pano, self.image):
                                print(fig)
                                print(self.Lines[fig])
                                self.Lines.pop(fig)
                                self.graph.delete_figure(fig)

                    # Erase all items
                    if values['-CLEAR-']:
                        print('clear')
                        self.graph.erase()
                        self.Lines = {}
                        self.PIL_pano = self.draw_image(self.panorama)
                        self.image = self.goto_frame(self.current_frame_num)


            # Calibration button was pressed
            if event == '-CALIB-':
                # Not currently in calibration mode
                if not self.calibrating:
                    self.calibrating = True
                    self.window['-CALIB-'].update(button_color=('#283b5b', 'white'))
                    self.text.update('Entered calibration mode! Drag or select a line of a known distance.', text_color='#fa8f13')
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
                        self.text.update('Please make a measurment by dragging a line!', text_color='white')
                        self.distance_units = 'Meters' if values['-METERS-'] else 'Feet and Inches'
                    else: # No calibration was ever done
                        self.calibrating = False
                        self.window['-CALIB-'].update(button_color=('white', '#283b5b'))
                        self.text.update('Please calibrate the ruler by dragging a line of a known distance.')
                        self.window['-LINE-'].update(False, disabled=True)

            
            if event == 'Debug':
                self.window.perform_long_operation(debug, '-DEBUG-')
                sg.show_debugger_popout_window()

            if event == '-DEBUG-':
                self.panorama, frame_dump = values[event]

                self.window.write_event_value('-LOCATOR DONE-', [(frame, None) for frame in frame_dump])

                # Make the progress bar
                self.progressbar = make_progressbar()

                # self.window.perform_long_operation(lambda : self.stitcher.locate_frames(self.panorama, frame_dump), '-LOCATOR DONE-')

            
            # Change cursor by toolbar selection
            if event == '-LINE-':
                self.cursor = 'cross'
                self.graph.set_cursor(self.cursor)
                self.text.update('Draw a line by dragging on the image.')
            if event == '-SELECT-':
                self.cursor = 'fleur'
                self.graph.set_cursor(self.cursor)
                self.text.update('Select the line to drag or resize.')
            if event == '-ERASE-':
                self.cursor = 'X_cursor'
                self.graph.set_cursor(self.cursor)
                self.text.update('Select a line to erase.')
            if event == '-CLEAR-':
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
                    self.EdgeCircles = {}

            if event == 'Back':
                # Setup the starting window
                self.window.close()
                del self.window

                self.window = make_window1()
                self.stitcher.set_window(self.window)

                # Reset the stitcher
                self.stitcher.set_max_match_num(self.max_match_num)
                self.stitcher.set_min_match_num(self.min_match_num)
                self.stitcher.set_f(self.f)
                self.stitcher.set_resize_factor(1)

                # Reset the working app
                self.PIL_pano = None
                self.image = None
                self.current_frame_num = 1
                self.magnify_id = None
                self.cross_id = None
                self.magnify_square_id = None # ID of magnifing square size visualizaion
                self.cursor = 'arrow'
                self.distance_units = 'px' # Units of distance
                self.dragging = False # True if there's a drag on the graph
                self.start_point = self.end_point = None # Items start and end
                self.lastxy = None # To calculate delta of movement
                self.last_figure = None # Last draw figure ID
                self.calibration_ratio = -1 # Known distance in units / pixel distance
                self.Lines = {} # Save all the line objects: self.Lines[line_id] = [(x1,y1), (x2, y2)]
                self.calibrating = False # Bool to tell if in calibration mode
                self.pixel_dist = -1 # line's pixel distance
                self.EdgeCircles = {} # Circles at edge of line to denote line selection. self.EdgeCircles[circle_id] = Circle. Circle = {'line_id': line_id, 'position': center_xy}

                self.stitcher.reset_stitcher()
                

        self.window.close()
        del self.window
        sys.exit()




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

    def update_distance(self, pixle_distance: int):
        '''
        Update the distance text based on the given distance.
        '''
        measurment = pixle_distance

        measurment_text = f'{pixle_distance:.5}{self.distance_units}' # In pixles

        if self.calibration_ratio > 0 and not self.calibrating:
            measurment = pixle_distance * self.calibration_ratio

            measurment_text = convert_to_units(measurment, self.distance_units)

        self.text.update(f'Distance: {measurment_text}')

    def enable_toolbox(self):
        '''
        Enable all the radio buttons.
        '''
        self.window['-LINE-'].update(True, disabled=False)
        self.window['-SELECT-'].update(disabled=False)
        self.window['-ERASE-'].update(disabled=False)
        self.window['-CLEAR-'].update(disabled=False)

    def calibration_toolbox(self):
        '''
        Disable all radio  buttons except the line.
        '''
        self.window['-LINE-'].update(True, disabled=False)
        self.window['-SELECT-'].update(disabled=False)
        self.window['-ERASE-'].update(disabled=True)
        self.window['-CLEAR-'].update(disabled=True)

    def select_and_move(self, figure_id: int, delta: Tuple[int, int]):
        '''
        Select the line by drawing circles at its edges, printing its size, and moving or resizing it if already selected.
        '''

        circle_radius = 3

        # Check if the circles belong to selected line
        if figure_id in [circle['line_id'] for circle in self.EdgeCircles.values()]:
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
            circle1 = {'line_id': figure_id}
            circle2 = {'line_id': figure_id}

            # Aquire the line's edges. point = (x, y)
            point_one, point_two = self.Lines[figure_id][:2]

            circle1['position'] = point_one
            circle2['position'] = point_two

            # Draw circles on graph
            circle1_id = self.graph.draw_circle(point_one, circle_radius,fill_color='#2d65a4', line_color='white')
            circle2_id = self.graph.draw_circle(point_two, circle_radius, fill_color='#2d65a4', line_color='white')

            # Save circles
            self.EdgeCircles[circle1_id] = circle1
            self.EdgeCircles[circle2_id] = circle2
            # print(self.EdgeCircles)

        line_length = self.Lines[figure_id][2] # In pixels
        line_length *= self.calibration_ratio if not self.calibrating else 1 # In self.calibration_ratio units

        self.text.update(f'Distance: {convert_to_units(line_length, self.distance_units)}')



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

                    # Update the image and the counter outside of the thread
                    self.window.write_event_value('-THREAD UPDATE-', frame)

                    # Send a graph event to update the magnifier
                    self.window.write_event_value('-GRAPH-+MOVE+', None)
                else:
                    time.sleep(0.35)
                    self.current_frame_num = 1


            # Neccecary sleep time (depending on FPS) to ensure smooth playback
            delay = self.delay - (time.time() - start_time)
            if delay < 0:
                # print("WOW!")
                delay = 0
            # print(f"        update time: {delay}")
            time.sleep(delay)
        # self.update()



    # --- GUI draw functions --- #

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
            image=PIL.Image.fromarray(frame, mode='RGBA').resize((self.graph_width, self.graph_height), PIL.Image.NEAREST)
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
            # we must mimic the way sg.Graph keeps a track of its added objects:
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
    heading_font = '_ 12 bold underline'
    text_font = '_ 10'

    def HelpText(text):
        return sg.Text(text, size=(80, None), font=text_font)

    help_why = \
""" Let's start with a review of the Goals of the PanoramicVideoStitching project (a.g the App)
1. To learn more
2. For you to be successful

This App solves a neccesity that came up in training, but could be expanded to many disciplines.

The App is here to help you improve your understanding of movement (and solve some debates). """

    help_goals = \
""" The goals of using this App are:
* Give you a better look of the subject's (athlete's) movement through space.
* Measure distances the athlete does.
* (?) Follow the subject's path through space.
* (?) Know the subject's speed and acceleration.
* (?) Know the force applied to the subject."""

    help_explain = \
""" The most obvious questions about this App's behavior
Q:  Why is my measurment trash?

A:  First of all ouch... (just note that even a perfect measurment is ±1% off)
    The short answer - There was a screw up somewhere.

    The longer answer - There are mainly 3 points of possible failiure:
    1. The panorama was built incorrectly (Oops my bad): I tried to write the most efficient code to build the panorama from just the video, with no information about the camera. That's A LOT of math and it's possible I made a mistake somewhere.

    2. There was a mistake in the calibration (just recalibrate): Any 2-3 pixels mistake in the line drawn can be quite noticable in the measurment. Do as best you can to place the calibration line correctly.

    3. There was a mistake in the measurment line (try and correct it): Again, 2-3 pixels off could be a huge diffrence.

    As you can imagine, no one mistake happens alone most the time, and those mistakes add up.

    So take all the measurments with a grain of salt. The App can gurentee you PB'd but it can help you learn much more about your technique.

Q:  The video is taking ages to load, I think you messed something up.

A:  Technically not a question but I'll allow it.
    There are 2 possibilities: either the video is too long or it isn't shot with strictly vertical movement.

    I won't get into details but the panorama-making algorithm assumes the video pretty much strictly and smoothly pans across a scene. Bascially if the cameraman jumps up and down with excitment the algorithm freaks out."""

    help_experience = \
""" I plan on adding some tinkering options for the more computer inclined folks out there."""
    help_steps = \
""" If there's any other problems that arise while using The App please let me know. Any small unexpected bahavior will be heavily looked on and exterminated so think long and hard before you take upon yourself the death of a bug. """
    layout = [
                [sg.T('Goals', font=heading_font, pad=(0,0))],
                [HelpText(help_goals)],
                [sg.T('Why?', font=heading_font, pad=(0,0))],
                [HelpText(help_why)],
                [sg.T('FAQ', font=heading_font, pad=(0,0))],
                [HelpText(help_explain)],
                [sg.T('Expert Mode (optional)', font=heading_font)],
                [HelpText(help_experience)],
                [sg.T('Steps', font=heading_font, pad=(0,0))],
                [HelpText(help_steps)],
                [sg.B('Close')]
              ]
    window = sg.Window('GUI Help', layout, keep_on_top=True, finalize=True)

    while window.read()[0] not in ('Close', None):
        continue
    window.close()
    del window

def debug() -> None:
    '''
    Jump to visual debugging.
    '''
    frames = []
    file_names = sorted(glob.glob('key_frames/frame_dump/frame[0-9][0-9]*.jpg'))
    for frame_name in file_names:
        img = cv2.imread(frame_name, cv2.IMREAD_UNCHANGED)
        if img is not None:
            frames.append(img)
    panorama = cv2.imread('key_frames/frame_dump/panorama.jpg')
    return (panorama, frames)

def expert_mode_window(stitcher: Stitcher, min_num: int, max_num:int, f: int):
    '''
    A window to chagne the stitcher's parameters.
    '''
    layout = [[sg.Text('min_match_num', size=(15,1)), sg.Input(default_text=f'{min_num}', size=(8,1), k='-MIN-', enable_events=True),
               sg.Text('?', size=(1,1), tooltip='Controls the minimum amount of overlap between\nstitched frames of the panorama.\nThe smaller min_match_num is, the lesser the overlap.\nIT\'S BETTER TO TINKER WITH max_match_num!')],
              [sg.Text('max_match_num', size=(15,1)), sg.Input(default_text=f'{max_num}', size=(8,1), k='-MAX-', enable_events=True),
               sg.Text('?', size=(1,1), tooltip='Controls the maximum amount of overlap between\nstitched frames of the panorama.\nThe larger max_match_num is, the greater the overlap.')],
              [sg.Text('f', size=(15,1)), sg.Input(default_text=f'{f}', size=(8,1), k='-F-', enable_events=True),
               sg.Text('?', size=(1,1), tooltip='Controls the focal point of the panorama.\nThe smaller f, the greater the curvature of the panorama.\nRule of Thumb: The greater the angle the camera pans,\nthe smaller f should be, and vice versa.')],
              [sg.Text('resize_factor', size=(15,1)), sg.Spin([i+1 for i in range(5)], initial_value=stitcher.get_resize_factor(), size=(4,1), k='-RESIZE-', enable_events=True, readonly=True),
               sg.Text('?', size=(1,1), tooltip='Controls the amount by which the quality decreases.\nBest for debuging and perfecting the\nappropriate parameters for a given video.')],
              [sg.B('Save')]]

    window = sg.Window('Expert Mode', layout, text_justification='c', finalize=True, resizable=True)

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
    layout = [[sg.ProgressBar(100, key='-PROGRESS BAR-', size=(100, 20)), sg.Text('0%', k='-PERCENT-', font=('Helvetica', 16))],
                [sg.Push(), sg.Text('Loading...', key='-TEXT-', font=('Helvetica', 16)), sg.Push()]]

    return sg.Window("Making the panorama...", layout, finalize=True, disable_close=True, keep_on_top=True)

def make_window1() -> sg.Window:
    '''
    Make window1 of browsing.
    '''
    col = [[sg.Push(), sg.B('More')],
           [sg.T('Select video', font='_ 16')],
           [sg.Text(font='_ 14', text='Please upload as short a video as you can.')],
           [sg.Text('Also pretty please, make sure the video is shot with minimal vertical movement (or else the algorithm freaks).', font='_ 14')],
           [sg.In(key="-FILEPATH-", enable_events=True), sg.FileBrowse("Browse", font='_ 14')],
           [sg.Debug('Debug', font='_ 14'), sg.B('Exit', k='-EXIT-', font='_ 14')]]

    layout = [[sg.Text(expand_x=True, expand_y=True, font='ANY 1', pad=(0, 0))],  # the thing that expands from top
              [sg.Column(col, element_justification='center' ,vertical_alignment='center', justification='center', expand_x=True, expand_y=True)]]

    return sg.Window("Panoramic Video Stitching", layout=layout, finalize=True, resizable=True, text_justification='c')


def make_window2() -> sg.Window:
    '''
    Make window2 of actual app functionality.
    '''
    col = [[sg.R('Draw Line', 1, k='-LINE-', enable_events=True, background_color="#526275", font=('Helvetica', 16), disabled=True)],
           [sg.R('Select Item', 1, k='-SELECT-', enable_events=True, background_color="#526275", font=('Helvetica', 16), disabled=True)],
           [sg.R('Erase Item', 1, k='-ERASE-', enable_events=True, background_color="#526275", font=('Helvetica', 16), disabled=True)],
           [sg.R('Erase All', 1, k='-CLEAR-', enable_events=True, background_color="#526275", font=('Helvetica', 16), disabled=True)]
           ]

    layout = [[sg.Push(), sg.B('Help')],
              [sg.Push(),
                sg.Slider(s=(30, 20), range=(0, 100), k="-SLIDER-", orientation="h",
                            enable_events=True, expand_x=True), sg.Text("0/0", k="-COUNTER-"),
                sg.B("Previous Frame", key="-P FRAME-", tooltip='[LEFT KEY]\n<-'), sg.B("Play", key="-PLAY-", tooltip='[SPACE]'),
                sg.B("Next Frame", key="-N FRAME-", tooltip='[RIGHT KEY]\n->'),
                sg.Push(), sg.Frame('Units',
                                    [[sg.R("Meters", 2, k='-METERS-', enable_events=True, font='_ 14'),
                                      sg.R("Feet and Inches", 2, k='-FEET-', enable_events=True, font='_ 14')
                                    ]], font='_ 16')
                ],
              [sg.Push(), sg.Graph((720,480), graph_bottom_left=(0,1080), key='-GRAPH-',
                            graph_top_right=(1920,0), background_color="black", motion_events=True, enable_events=True,
                            drag_submits=True),
                sg.Push()],
              [sg.Push(),
                sg.Frame('Magnifier', [[sg.Slider(range=(100, 10), default_value=40, resolution=10, orientation='v',
                                            enable_events=True, k='-MAGNIFY SIZE-', tooltip='Magnifing Area'),
                                         sg.Graph((200,200), graph_bottom_left=(0,200), key='-MAGNIFY-',
                                            graph_top_right=(200,0), background_color="light grey")
                                       ]], font='_ 14'),
                sg.Frame('Toolbar',col, k='-COL-', size=(150, 225), background_color="#526275", font='_ 14'), sg.Push(),
                sg.Col([[sg.VPush()],
                        [sg.Text('Please calibrate the ruler by dragging a line of a known distance.', font='_ 16',
                            k='-TEXT-', text_color='#fa8f13', s=(50,1), justification='c')],
                        [sg.Push(), sg.B('Calibrate', k='-CALIB-'), sg.Push()],
                        [sg.VPush()]], element_justification='c'),
                sg.Push(), sg.VPush()
                ],
              [sg.B('Back'), sg.Push(), sg.B('Exit')]
              ]

    return sg.Window("Panoramic Video Stitching", layout=layout, finalize=True, resizable=True)

def popup_get_distance() -> Tuple[Union[float, None], str]:
    '''
    Get the distance given by the user.
    '''
    value = -1
    distance_units = ''

    layout = [[sg.Text('Please provide the distance of the drawn line as you know it', auto_size_text=True)],
               [sg.InputText(key='-IN-', enable_events=True, default_text='0[.0]', text_color='gray', focus=False),
                sg.Combo(['Meters', 'Feet and Inches'], default_value='Meters', k='-UNITS-', enable_events=True, readonly=True)
              ],
               [sg.Button('Ok', size=(6, 1), bind_return_key=True, focus=True), sg.Button('Cancel', size=(6, 1), bind_return_key=True)]]

    window = sg.Window(title='Get Distance', layout=layout,  auto_size_text=True, keep_on_top=True, finalize=True, modal=True)
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
                    value = float(distance)
                except ValueError:
                    window['-IN-'].update('')
                    window.write_event_value('-IN-+FOCUS OUT+', None)
                    continue
            break

    window.close()
    del window

    return value, distance_units

def convert_to_units(measurment: Union[int, float], units: str) -> str:
    '''
    Convert distance in {units} to its units.
    '''
    measurment_text = ''
    if units == 'Feet and Inches':
        feet = int(measurment // 12)
        inches = int(measurment % 12)
        fraction_inch = Fraction(int(8 * ((measurment % 12) - inches)), 8) if measurment % 12 != 0 else 0

        measurment_text = f'{feet}\'{inches} {fraction_inch}" {units}' if fraction_inch != 0 else f'{feet}\'{inches}" {units}'
    else: # Meters or pixels
        measurment_text = f'{measurment:.5} {units}'
    return measurment_text

GUI()
