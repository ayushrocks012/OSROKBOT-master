import pytesseract
from Actions.action import Action
from config_manager import ConfigManager
from PIL import Image, ImageOps

DEFAULT_ANTIALIAS_METHOD = getattr(Image, "Resampling", Image).LANCZOS


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
            print(text)
            if (self.description == "marchcount"):
                #first char from text to int
                if (int(text[0])<int(text[2])):
                    print("March not full")
                    return True
                print("March full")
                return False
        except Exception as e:
            print(f"Exception during extraction: {e}")
            return True
        text = pytesseract.image_to_string(img, lang='eng', config='--oem 3 --psm 6 -c tessedit_char_blacklist=|')
        text = text.replace("\n", "")
        print(text)
        if context:
            context.set_extracted_text(self.description, text)
        else:
            print("Warning: No context provided to ExtractTextAction.")
        

        


        
        return True
