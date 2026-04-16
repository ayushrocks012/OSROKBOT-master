import pytesseract
from Actions.action import Action
from config_manager import ConfigManager
from logging_config import get_logger
from PIL import Image, ImageOps

DEFAULT_ANTIALIAS_METHOD = getattr(Image, "Resampling", Image).LANCZOS
LOGGER = get_logger(__name__)


def configure_tesseract():
    config = ConfigManager()
    tesseract_path = config.get("TESSERACT_PATH")
    if tesseract_path:
        pytesseract.pytesseract.tesseract_cmd = tesseract_path
    return getattr(Image, config.get("ANTIALIAS_METHOD", "LANCZOS") or "LANCZOS", DEFAULT_ANTIALIAS_METHOD)



# You can now use the preprocessed image in your existing code

class ExtractTextAction(Action):
    def __init__(self, image_path="test.png", description="", aggregate=False, delay=0, post_delay =0):
        super().__init__(delay=delay, post_delay=post_delay)
        self.image_path = image_path
        self.description = description
        self.aggregate = aggregate

    def preprocess_image(self, image_path):
        antialias_method = configure_tesseract()
        # Open the image file
        img = Image.open(image_path)

        # Convert to grayscale
        img = img.convert('L')


        width, height = img.size
        img = img.resize((width*5, height*5), antialias_method)
        # Binarization
        if "Q" in self.description:
            img = img.point(lambda x: 0 if x < 140 else 255, '1')
        elif self.description == "marchcount":
            img = img.point(lambda x: 0 if x < 145 else 255, '1')
            img = ImageOps.invert(img)
        else:
            img = img.point(lambda x: 0 if x < 195 else 255, '1')
            img = ImageOps.invert(img)

        # Save the processed image
        img.save("testprocessed.png")
        
        return img

    def execute(self, context=None):
        
        img = self.preprocess_image(self.image_path)
        try:
            text = pytesseract.image_to_string(img, lang='eng', config="--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789//")
            LOGGER.info("Extracted numeric text: %s", text)
            if (self.description == "marchcount"):
                #first char from text to int
                if (int(text[0])<int(text[2])):
                    LOGGER.info("March not full")
                    return True
                LOGGER.info("March full")
                return False
        except Exception as e:
            LOGGER.warning("Exception during extraction: %s", e)
            return True
        text = pytesseract.image_to_string(img, lang='eng', config='--oem 3 --psm 6 -c tessedit_char_blacklist=|')
        text = text.replace("\n", "")
        LOGGER.info("Extracted text: %s", text)
        if context:
            context.set_extracted_text(self.description, text)
        else:
            LOGGER.warning("No context provided to ExtractTextAction.")
        

        


        
        return True
