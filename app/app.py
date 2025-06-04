from flask import Flask, render_template, request, redirect, url_for, send_file
import os
import tempfile
import subprocess
from utils.latex_generator import generate_latex_document

app = Flask(__name__)

# Временное хранилище для карточек в рамках сессии
# В реальном приложении это должно быть заменено на базу данных
session_cards = []

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html', cards_count=len(session_cards))

@app.route('/add_card', methods=['POST'])
def add_card():
    front = request.form.get('front', '')
    back = request.form.get('back', '')
    
    if front and back:
        session_cards.append({'front': front, 'back': back})
    
    return redirect(url_for('index'))

@app.route('/reset', methods=['POST'])
def reset():
    global session_cards
    session_cards = []
    return redirect(url_for('index'))

@app.route('/generate', methods=['POST'])
def generate():
    if not session_cards:
        return render_template('index.html', error='Добавьте хотя бы одну карточку!')
    
    # Округляем количество карточек до кратного 8
    cards_count = len(session_cards)
    required_count = ((cards_count + 7) // 8) * 8
    
    # Дополняем пустыми карточками, если нужно
    for i in range(cards_count, required_count):
        session_cards.append({'front': f'Карточка {i+1} (пустая)', 'back': ''})
    
    # Генерируем LaTeX документ
    latex_content = generate_latex_document(session_cards)
    
    # Создаем временный каталог для генерации PDF
    with tempfile.TemporaryDirectory() as temp_dir:
        # Путь к .tex файлу
        tex_path = os.path.join(temp_dir, 'cards.tex')
        
        # Записываем содержимое в .tex файл
        with open(tex_path, 'w', encoding='utf-8') as tex_file:
            tex_file.write(latex_content)
        
        # Скомпилируем LaTeX в PDF
        try:
            subprocess.run(['pdflatex', '-output-directory', temp_dir, tex_path], 
                          check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            pdf_path = os.path.join(temp_dir, 'cards.pdf')
            
            if os.path.exists(pdf_path):
                return send_file(pdf_path, 
                                as_attachment=True, 
                                download_name='physics_cards.pdf')
            else:
                return render_template('index.html', 
                                      error='Ошибка при создании PDF файла!', 
                                      cards_count=len(session_cards))
                
        except subprocess.CalledProcessError:
            return render_template('index.html', 
                                 error='Ошибка компиляции LaTeX!', 
                                 cards_count=len(session_cards))

@app.route('/preview_latex', methods=['POST'])
def preview_latex():
    if not session_cards:
        return render_template('index.html', error='Добавьте хотя бы одну карточку!')
    
    # Округляем количество карточек до кратного 8
    cards_count = len(session_cards)
    required_count = ((cards_count + 7) // 8) * 8
    
    # Дополняем пустыми карточками, если нужно
    for i in range(cards_count, required_count):
        session_cards.append({'front': f'Карточка {i+1} (пустая)', 'back': ''})
    
    # Генерируем LaTeX документ
    latex_content = generate_latex_document(session_cards)
    
    return render_template('result.html', latex_content=latex_content)

if __name__ == '__main__':
    app.run(debug=True)

