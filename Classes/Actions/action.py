from abc import ABC, abstractmethod
from window_handler import WindowHandler
import global_vars
import time

class Action(ABC):
    def __init__(self, skip_check_first_time: bool):
        self.skip_check_first_time = skip_check_first_time
        self.first_run = True
        self.performance_multiplier = 1


    def perform(self):
        
        tempString=""
        try:  
            if self.image != "Media/captchachest.png":
                tempString = self.__class__.__name__+ "\n"
                tempString+=str(self.image)+ "\n"
                tempString+=str(self.delay)+ "s delay\n"
                tempString+=str(self.post_delay)+ "s post_delay\n"
        except AttributeError:
            try:  
                tempString = self.__class__.__name__+ "\n"
                tempString+=str(self.key)+ "\n"
                tempString+=str(self.delay)+ "s delay\n"
                tempString+=str(self.post_delay)+ "s post_delay\n"
            except AttributeError:
                tempString = self.__class__.__name__+ "\n"
                tempString+="\n"
                tempString+= str(self.delay)+ "s delay\n"
                tempString+=str(self.post_delay)+ "s post_delay\n"

        
        if (tempString != ""):
            global_vars.GlobalVars().UI.OS_ROKBOT.signal_emitter.state_changed.emit(tempString.replace("action","").replace("FindAnd","").replace(".png","").replace("Media/","").replace("Action",""))     
        time.sleep(self.delay)
        result = self.execute()
        time.sleep(self.post_delay)
        return result