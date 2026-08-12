import streamlit as st
import os, json, math, random, re
import PIL
from PIL import Image, ImageDraw, ImageFont

# ==========================================
# CORREÇÃO DO ERRO DO PILLOW (ANTIALIAS)
# ==========================================
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = getattr(Image, 'Resampling', Image).LANCZOS

import numpy as np
from moviepy.editor import VideoClip, AudioFileClip, VideoFileClip
from moviepy.video.fx.all import crop
from pypdf import PdfReader
from google import genai
from google.genai import types
import requests
import gc

# ==========================================
# CONFIGURAÇÕES E CHAVES DE API
# ==========================================
API_GEMINI = st.secrets["API_GEMINI"] if "API_GEMINI" in st.secrets else "AQ.Ab8RN6K1lScP9JN2iGJcIKY44kuGxQQTaiOnRJeEgXzhLOSzIw"
API_FISH = st.secrets["API_FISH"] if "API_FISH" in st.secrets else "2bc700daad0e478cb67da9d7f89dba75"
API_DEEPGRAM = st.secrets["API_DEEPGRAM"] if "API_DEEPGRAM" in st.secrets else "5d492daf0a6756920b2456119f32ac790af6ede9"
MODELO_GEMINI = "gemini-flash-latest"

st.set_page_config(page_title="AutoTube Concursos", layout="wide", page_icon="📚")

PASTAS = ["base_conhecimento_pdfs", "banco_de_midias", "roteiros/feitos", "output", "assets", "Fundos"]
for p in PASTAS:
    os.makedirs(p, exist_ok=True)

for json_file in ["base_conhecimento.json", "dicionario_fonetico.json", "vozes_salvas.json"]:
    if not os.path.exists(json_file):
        with open(json_file, 'w', encoding='utf-8') as f:
            f.write('{}')

# ==========================================
# FUNÇÕES AUXILIARES
# ==========================================
def extract_text_from_pdf(pdf_path):
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text() + "\n"
    return text

def load_knowledge_base():
    if not os.path.exists("base_conhecimento.json"):
        return {}
    with open("base_conhecimento.json", "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_knowledge_base(data):
    with open("base_conhecimento.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

def normalizar_texto(texto):
    return re.sub(r'[^\w\s]', '', texto).strip().lower()

def gerar_audio_fishaudio(texto, dicionario_global, output_path, api_key, voice_id="8d8c7204f55f440abf975500590c3c12"):
    for orig, fon in dicionario_global.items(): 
        texto = texto.replace(orig, fon)
        
    url = "https://api.fish.audio/v1/tts"
    headers = {
        "Authorization": f"Bearer {api_key}", 
        "Content-Type": "application/json",
        "model": "s2.1-pro-free" 
    }
    payload = {
        "text": texto, 
        "reference_id": voice_id, 
        "format": "mp3"
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            with open(output_path, "wb") as f: 
                f.write(response.content)
            return output_path, None
        else:
            return None, f"Status {response.status_code}: {response.text}"
    except Exception as e:
        return None, f"Erro de Conexão Local: {str(e)}"

def obter_timestamps_deepgram(audio_path, api_key):
    url = 'https://api.deepgram.com/v1/listen?language=pt-BR&model=nova-2&smart_format=true'
    headers = {
        'Authorization': f'Token {api_key}',
        'Content-Type': 'audio/mpeg'
    }
    
    try:
        with open(audio_path, 'rb') as audio:
            response = requests.post(url, headers=headers, data=audio)
            
            if response.status_code == 200:
                data = response.json()
                try:
                    words = data['results']['channels'][0]['alternatives'][0]['words']
                    if not words:
                        return None, "Status 200, mas lista de palavras veio vazia."
                    return words, None
                except KeyError:
                    return None, f"Estrutura de resposta inesperada: {data}"
            else:
                erro_msg = f"Status {response.status_code}: {response.text}"
                return None, erro_msg
    except Exception as e:
        return None, f"Erro de Conexão Deepgram: {str(e)}"

def sincronizar_palavras(script_keywords, deepgram_words, duration):
    synced_kws = []
    
    if not deepgram_words:
        for kw in script_keywords:
            synced_kws.append({
                'texto': kw['texto'],
                'inicio': kw.get('inicio_porcentagem', 0) * duration,
                'fim': kw.get('fim_porcentagem', 0.1) * duration
            })
        return synced_kws
        
    dg_texts = [normalizar_texto(w.get('punctuated_word', w.get('word', ''))) for w in deepgram_words]
    
    for kw in script_keywords:
        kw_text_original = kw['texto']
        kw_norm_words = normalizar_texto(kw_text_original).split()
        
        if not kw_norm_words:
            continue
            
        encontrado = False
        for i in range(len(dg_texts) - len(kw_norm_words) + 1):
            match = True
            for j, word in enumerate(kw_norm_words):
                if dg_texts[i+j] != word:
                    match = False
                    break
            if match:
                start_time = deepgram_words[i]['start']
                end_time = deepgram_words[i + len(kw_norm_words) - 1]['end']
                synced_kws.append({
                    'texto': kw_text_original,
                    'inicio': start_time,
                    'fim': end_time
                })
                encontrado = True
                break
        
        if not encontrado:
            synced_kws.append({
                'texto': kw_text_original,
                'inicio': kw.get('inicio_porcentagem', 0) * duration,
                'fim': kw.get('fim_porcentagem', 0.1) * duration
            })
            
    return synced_kws

def render_keyword_video(script_data, audio_path, output_path, config_visual, deepgram_words=None):
    videos_fundo = [f for f in os.listdir("Fundos") if f.lower().endswith(('.mp4', '.mov'))]
    if not videos_fundo:
        raise FileNotFoundError("Por favor, coloque pelo menos um vídeo .mp4 na pasta 'Fundos'.")

    caminho_fundo = os.path.join("Fundos", random.choice(videos_fundo))
    audio_clip = AudioFileClip(audio_path)
    duration = audio_clip.duration
    
    bg_clip = VideoFileClip(caminho_fundo)
    if bg_clip.h != 1920 or bg_clip.w != 1080:
        bg_clip = bg_clip.resize(height=1920)
        bg_clip = crop(bg_clip, x_center=bg_clip.w/2, y_center=bg_clip.h/2, width=1080, height=1920)
    
    keywords = script_data.get("palavras_chave", [])
    
    synced_keywords = sincronizar_palavras(keywords, deepgram_words, duration)
    
    cor_primaria = hex_to_rgb(config_visual['cor_primaria'])
    cor_secundaria = hex_to_rgb(config_visual['cor_secundaria'])
    nome_fonte = config_visual['fonte']
    estilo_design = config_visual['estilo']

    rendered_kws = []
    
    for kw in synced_keywords:
        texto = kw['texto'].upper()
        
        tamanho_fonte = config_visual.get('tamanho_fonte', 130)
        try:
            font_kw = ImageFont.truetype(nome_fonte, tamanho_fonte)
        except:
            font_kw = ImageFont.load_default()
            
        temp_img = Image.new('RGBA', (1080, 500), (0,0,0,0))
        d_temp = ImageDraw.Draw(temp_img)
        
        try:
            bbox = d_temp.textbbox((0, 0), texto, font=font_kw)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except AttributeError:
            tw, th = 800, 150

        while tw > 900 and tamanho_fonte > 40:
            tamanho_fonte -= 5
            try:
                font_kw = ImageFont.truetype(nome_fonte, tamanho_fonte)
                bbox = d_temp.textbbox((0, 0), texto, font_kw)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
            except AttributeError:
                break
            
        pad_x, pad_y = 40, 20
        box_w, box_h = tw + pad_x*2, th + pad_y*2
        
        kw_img = Image.new('RGBA', (box_w + 80, box_h + 80), (0,0,0,0))
        d_kw = ImageDraw.Draw(kw_img)
        
        if "1" in estilo_design: 
            d_kw.text((40 + pad_x, 40 + pad_y - 15), texto, font=font_kw, fill=cor_primaria, stroke_width=6, stroke_fill=cor_secundaria)
        elif "2" in estilo_design: 
            d_kw.rectangle([40, 40, box_w+40, box_h+40], fill=cor_secundaria, outline=cor_primaria, width=6)
            d_kw.text((40 + pad_x, 40 + pad_y - 15), texto, font=font_kw, fill=cor_primaria)
        else: 
            d_kw.rectangle([40, 40, box_w+40, box_h+40], fill=cor_primaria)
            d_kw.text((40 + pad_x, 40 + pad_y - 15), texto, font=font_kw, fill=cor_secundaria)
        
        angulo = random.uniform(-2, 2)
        kw_img = kw_img.rotate(angulo, expand=True, resample=Image.Resampling.BICUBIC)
        
        rendered_kws.append({
            'inicio': kw['inicio'],
            'fim': kw['fim'],
            'img': kw_img
        })

    def make_frame(t):
        bg_t = t % bg_clip.duration
        bg_frame = bg_clip.get_frame(bg_t)
        img = Image.fromarray(bg_frame).convert('RGBA')

        for kw_data in rendered_kws:
            if kw_data['inicio'] <= t <= kw_data['fim']:
                dt = t - kw_data['inicio']
                
                if dt < 0.2: 
                    scale = 1.0 + 0.4 * math.exp(-20 * dt) * math.sin(40 * dt)
                    if dt < 0.05:
                        scale = dt * 20 
                else:
                    scale = 1.0
                
                kw_img = kw_data['img']
                new_w = int(kw_img.width * scale)
                new_h = int(kw_img.height * scale)
                
                if new_w > 0 and new_h > 0:
                    resized = kw_img.resize((new_w, new_h), Image.Resampling.BILINEAR)
                    x = 540 - new_w // 2
                    y = 960 - new_h // 2
                    img.paste(resized, (x, y), resized)
                break 

        return np.array(img.convert('RGB'))

    video = VideoClip(make_frame, duration=duration)
    video = video.set_audio(audio_clip)
    
    # Reduzido para threads=2 para evitar crash no Streamlit Cloud
    video.write_videofile(
        output_path, 
        fps=24, 
        codec="libx264", 
        audio_codec="aac",
        threads=2, 
        preset="ultrafast"
    )
    
    bg_clip.close()
    audio_clip.close()
    video.close()
    return output_path

# ==========================================
# INTERFACE DO STREAMLIT
# ==========================================
st.sidebar.title("⚙️ Sistema Ativo")
st.sidebar.success("✅ Chaves de API Integradas")

st.sidebar.markdown("---")
st.sidebar.header("🎨 Customização Visual")
fonte_selecionada = st.sidebar.selectbox("Tipografia (Fonte)", ["impact.ttf", "arialbd.ttf", "courbd.ttf", "comic.ttf", "tahoma.ttf", "trebucbd.ttf"])
estilo_selecionado = st.sidebar.selectbox("Estilo do Design", ["Estilo 1 - Texto Glitch (Vazado)", "Estilo 2 - Caixa Cyber (Contorno)", "Estilo 3 - Bloco Sólido (Invertido)"])
tamanho_fonte_base = st.sidebar.slider("Tamanho da Fonte", min_value=50, max_value=250, value=130, step=10)

col1, col2 = st.sidebar.columns(2)
with col1:
    cor_primaria = st.color_picker("Cor Primária", "#0AFF41")
with col2:
    cor_secundaria = st.color_picker("Cor Secundária", "#0F0F0F")

configuracoes_visuais = {
    "fonte": fonte_selecionada,
    "estilo": estilo_selecionado,
    "cor_primaria": cor_primaria,
    "cor_secundaria": cor_secundaria,
    "tamanho_fonte": tamanho_fonte_base
}

st.sidebar.markdown("---")
menu = st.sidebar.radio("Navegação", [
    "📖 Base de Conhecimento", 
    "📁 Gerenciador de Mídias", 
    "✍️ Gerador de Roteiros (Palavras-chave)", 
    "🃏 Gerador de Flashcards",
    "🎬 Renderizador Individual",
    "🏭 Renderização em Massa",
    "📥 Meus Vídeos (Output)",
    "💾 Backup e Restauração"
])

if menu == "📖 Base de Conhecimento":
    st.header("🧠 Base de Conhecimento Estruturada")
    
    # --- UPLOAD E EXTRAÇÃO ---
    st.subheader("1. Ingerir novo material (PDF)")
    materia_nome = st.text_input("Nome da Matéria (ex: AFO, Contabilidade)")
    uploaded_file = st.file_uploader("Envie o PDF (A IA vai ler e separar por tópicos)", type="pdf")
    
    if st.button("Processar e Estruturar PDF"):
        if uploaded_file and materia_nome:
            with st.spinner("Lendo PDF e estruturando tópicos com IA... Isso pode levar um minuto."):
                texto_completo = extract_text_from_pdf(uploaded_file)
                
                # Pedimos pro Gemini fatiar o texto em Tópicos didáticos
                prompt_estruturacao = f"""
                Analise o texto abaixo retirado de um material de estudo de concurso público.
                Extraia os principais tópicos e seus respectivos conteúdos detalhados.
                Retorne estritamente um arquivo JSON onde as chaves são os Títulos dos Tópicos e os valores são os textos resumidos/estruturados de cada tópico.
                Exemplo de formato esperado:
                {{
                    "Orçamento Público - Conceitos Iniciais": "Conteúdo explicativo detalhado...",
                    "Princípios Orçamentários": "Conteúdo explicativo detalhado..."
                }}
                
                Texto:
                {texto_completo[:100000]}
                """
                try:
                    client = genai.Client(api_key=API_GEMINI)
                    response = client.models.generate_content(
                        model=MODELO_GEMINI,
                        contents=prompt_estruturacao,
                        config=types.GenerateContentConfig(response_mime_type="application/json"),
                    )
                    topicos_extraidos = json.loads(response.text)
                    
                    kb = load_knowledge_base()
                    if materia_nome not in kb:
                        kb[materia_nome] = {}
                    
                    # Itera sobre os novos tópicos para somar ou criar
                    for topico, novo_conteudo in topicos_extraidos.items():
                        if topico in kb[materia_nome]:
                            # Se o assunto já existe, adiciona uma quebra de linha e soma o novo conteúdo
                            kb[materia_nome][topico] += f"\n\n--- NOVO MATERIAL ADICIONADO ---\n\n{novo_conteudo}"
                        else:
                            # Se é um assunto novo, apenas cria
                            kb[materia_nome][topico] = novo_conteudo
                            
                    save_knowledge_base(kb)
                    
                    st.success(f"{len(topicos_extraidos)} tópicos extraídos e salvos com sucesso!")
                except Exception as e:
                    st.error(f"Erro ao estruturar com IA: {e}")
        else:
            st.warning("Preencha o nome da matéria e insira o PDF.")

    # --- VISUALIZAÇÃO E EDIÇÃO ---
    st.markdown("---")
    st.subheader("2. Explorar e Editar Base")
    kb = load_knowledge_base()
    
    if kb:
        materia_selecionada = st.selectbox("Selecione a Matéria para visualizar", list(kb.keys()))
        if materia_selecionada:
            topicos = kb[materia_selecionada]
            
            # Adicionar novo tópico manualmente
            with st.expander("➕ Adicionar Assunto Manualmente"):
                novo_nome = st.text_input("Nome do novo assunto")
                novo_conteudo = st.text_area("Conteúdo base do assunto")
                if st.button("Salvar Novo Assunto"):
                    if novo_nome and novo_conteudo:
                        kb[materia_selecionada][novo_nome] = novo_conteudo
                        save_knowledge_base(kb)
                        st.success("Assunto adicionado!")
                        st.rerun()
                        
            st.write(f"### Assuntos Cadastrados em {materia_selecionada}")
            for topico, conteudo in topicos.items():
                with st.expander(f"📚 {topico}"):
                    # Área de texto editável para o conteúdo
                    conteudo_editado = st.text_area("Texto Base (Edite se quiser adicionar suas próprias anotações):", 
                                                    value=conteudo, height=200, 
                                                    key=f"edit_{materia_selecionada}_{topico}")
                    col1, col2 = st.columns([1, 4])
                    with col1:
                        if st.button("💾 Salvar Edição", key=f"btn_{materia_selecionada}_{topico}"):
                            kb[materia_selecionada][topico] = conteudo_editado
                            save_knowledge_base(kb)
                            st.success("Salvo!")
                    with col2:
                        if st.button("🗑️ Apagar Assunto", key=f"del_{materia_selecionada}_{topico}"):
                            del kb[materia_selecionada][topico]
                            save_knowledge_base(kb)
                            st.rerun()
    else:
        st.info("A base de conhecimento está vazia. Cadastre sua primeira matéria enviando um PDF.")

elif menu == "📁 Gerenciador de Mídias":
    st.header("Gerenciador por Disciplinas")
    nova_disciplina = st.text_input("Nova Disciplina")
    if st.button("Criar Pasta"):
        os.makedirs(os.path.join("banco_de_midias", nova_disciplina.lower().replace(" ", "_")), exist_ok=True)
        st.success(f"Pasta {nova_disciplina} criada!")
    pastas = os.listdir("banco_de_midias")
    if pastas:
        st.selectbox("Selecione a Disciplina para gerenciar", pastas)
        st.file_uploader("Adicionar mídia (B-roll)", accept_multiple_files=True)

elif menu == "✍️ Gerador de Roteiros (Palavras-chave)":
    st.header("Gerar Roteiros Virais com IA")
    
    kb = load_knowledge_base()
    if not kb:
        st.warning("Vá na Base de Conhecimento e cadastre ou adicione materiais primeiro!")
    else:
        modo_geracao = st.radio("Modo de Geração", ["Individual", "Em Massa (Automático)"], horizontal=True)
        st.markdown("---")
        
        materia_selecionada = st.selectbox("Qual Matéria?", list(kb.keys()))
        topicos = list(kb[materia_selecionada].keys())
        
        if not topicos:
            st.warning("Essa matéria não tem tópicos cadastrados.")
        else:
            if modo_geracao == "Individual":
                topico_selecionado = st.selectbox("Qual Assunto Base?", topicos)
                foco_especifico = st.text_input("Qual o foco específico? (Ex: Pegadinhas Cebraspe, Foco em exceções) - Opcional")
                
                texto_base = kb[materia_selecionada][topico_selecionado]
                
                if st.button("Gerar Estrutura (JSON)"):
                    with st.spinner(f"Criando roteiro fluido sobre {topico_selecionado}..."):
                        prompt = f'''
                        Você é um roteirista e professor especializado em vídeos curtos virais (Shorts/Reels/TikTok) para concurseiros de alto nível (área fiscal e controle).
                        Com base no texto fornecido, crie um roteiro didático, dinâmico e MUITO FLUIDO de até 60 segundos sobre: '{topico_selecionado}'.
                        Foco da abordagem (se houver): {foco_especifico}
                        
                        DIRETRIZES DE TOM DE VOZ E FLUIDEZ (MUITO IMPORTANTE):
                        - O tom deve ser de uma conversa direta e informal com o aluno, como se você estivesse dando uma "dica de ouro" de bastidor.
                        - Quebre a formalidade de textos acadêmicos ou leis.
                        - OBRIGATORIAMENTE, utilize expressões de transição e ganchos conversacionais no início ou no meio das explicações. Use frases como:
                          "Bom, então vamos lá..."
                          "Não sei se você sabe, mas..."
                          "Isso aqui parece besteira, mas muita gente erra na prova..."
                          "Olha só..."
                          "Bom, seguinte..."
                          "Então, o que acontece é que..."
                        - Vá direto ao ponto técnico logo após puxar a atenção com essas expressões.

                        A saída DEVE ser estritamente em JSON, seguindo a estrutura abaixo.
                        Na chave 'palavras_chave', selecione termos importantes (MÁXIMO DE 3 PALAVRAS POR TERMO) 
                        que devem piscar na tela ao longo do vídeo para reter a atenção. 
                        ATENÇÃO: NUNCA coloque os conectivos conversacionais nas palavras-chave.
                        
                        - 'inicio_porcentagem': Quando a palavra aparece (0.0 a 1.0)
                        - 'fim_porcentagem': Quando ela desaparece (0.0 a 1.0)
                        Mantenha um fluxo lógico para acompanhar a narração.
                        
                        {{
                          "roteiro_falado": "Bom, seguinte... [Texto completo com tom fluido e conversacional]",
                          "palavras_chave": [
                            {{"texto": "TERMO TÉCNICO", "inicio_porcentagem": 0.05, "fim_porcentagem": 0.15}}
                          ]
                        }}
                        
                        Texto base extraído do material:
                        {texto_base}
                        '''
                        try:
                            client = genai.Client(api_key=API_GEMINI)
                            response = client.models.generate_content(
                                model=MODELO_GEMINI,
                                contents=prompt,
                                config=types.GenerateContentConfig(response_mime_type="application/json"),
                            )
                            roteiro_json = response.text
                            
                            nome_limpo = re.sub(r'[^\w\-]', '_', topico_selecionado).lower()
                            caminho_salvar = os.path.join("roteiros", f"roteiro_{nome_limpo}.json")
                            
                            with open(caminho_salvar, 'w', encoding='utf-8') as f:
                                f.write(roteiro_json)
                                
                            st.success("Roteiro e Decupagem Gerados com Sucesso!")
                            st.json(json.loads(roteiro_json))
                        except Exception as e:
                            st.error(f"Erro no Gemini: {e}")
                            
            else: # Em Massa (Automático)
                st.info(f"A matéria **{materia_selecionada}** possui **{len(topicos)}** assuntos estruturados.")
                qtd_roteiros = st.slider("Quantos roteiros deseja gerar em sequência?", 1, len(topicos), min(3, len(topicos)))
                foco_especifico = st.text_input("Qual o foco específico para todos? (Ex: Foco Cebraspe) - Opcional")
                
                if st.button(f"🚀 Gerar {qtd_roteiros} Roteiros Automaticamente"):
                    # Seleciona aleatoriamente a quantidade desejada de tópicos para não gerar sempre os mesmos
                    topicos_selecionados = random.sample(topicos, qtd_roteiros)
                    
                    barra_progresso = st.progress(0)
                    texto_status = st.empty()
                    
                    for idx, topico in enumerate(topicos_selecionados):
                        texto_status.markdown(f"**Processando ({idx+1}/{qtd_roteiros}):** {topico}...")
                        texto_base = kb[materia_selecionada][topico]
                        
                        prompt_massa = f'''
                        Você é um roteirista e professor especializado em vídeos curtos virais (Shorts/Reels/TikTok) para concurseiros de alto nível (área fiscal e controle).
                        Com base no texto fornecido, crie um roteiro didático, dinâmico e MUITO FLUIDO de até 60 segundos sobre: '{topico}'.
                        Foco da abordagem (se houver): {foco_especifico}
                        
                        DIRETRIZES DE TOM DE VOZ E FLUIDEZ (MUITO IMPORTANTE):
                        - O tom deve ser de uma conversa direta e informal com o aluno, como se você estivesse dando uma "dica de ouro" de bastidor.
                        - Quebre a formalidade de textos acadêmicos ou leis.
                        - OBRIGATORIAMENTE, utilize expressões de transição e ganchos conversacionais no início ou no meio das explicações. Use frases como:
                          "Bom, então vamos lá..."
                          "Não sei se você sabe, mas..."
                          "Isso aqui parece besteira, mas muita gente erra na prova..."
                          "Olha só..."
                          "Bom, seguinte..."
                          "Então, o que acontece é que..."
                        - Vá direto ao ponto técnico logo após puxar a atenção com essas expressões.

                        A saída DEVE ser estritamente em JSON, seguindo a estrutura abaixo.
                        Na chave 'palavras_chave', selecione termos importantes (MÁXIMO DE 3 PALAVRAS POR TERMO) 
                        que devem piscar na tela ao longo do vídeo para reter a atenção. 
                        ATENÇÃO: NUNCA coloque os conectivos conversacionais nas palavras-chave.
                        
                        - 'inicio_porcentagem': Quando a palavra aparece (0.0 a 1.0)
                        - 'fim_porcentagem': Quando ela desaparece (0.0 a 1.0)
                        Mantenha um fluxo lógico para acompanhar a narração.
                        
                        {{
                          "roteiro_falado": "Bom, seguinte... [Texto completo com tom fluido e conversacional]",
                          "palavras_chave": [
                            {{"texto": "TERMO TÉCNICO", "inicio_porcentagem": 0.05, "fim_porcentagem": 0.15}}
                          ]
                        }}
                        
                        Texto base extraído do material:
                        {texto_base}
                        '''
                        
                        try:
                            client = genai.Client(api_key=API_GEMINI)
                            response = client.models.generate_content(
                                model=MODELO_GEMINI,
                                contents=prompt_massa,
                                config=types.GenerateContentConfig(response_mime_type="application/json"),
                            )
                            roteiro_json = response.text
                            
                            nome_limpo = re.sub(r'[^\w\-]', '_', topico).lower()
                            caminho_salvar = os.path.join("roteiros", f"roteiro_{nome_limpo}.json")
                            
                            with open(caminho_salvar, 'w', encoding='utf-8') as f:
                                f.write(roteiro_json)
                                
                        except Exception as e:
                            st.error(f"Erro ao gerar o tópico '{topico}': {e}")
                            
                        # Atualiza barra de progresso
                        barra_progresso.progress((idx + 1) / qtd_roteiros)
                        
                    texto_status.success(f"✅ Geração concluída! {qtd_roteiros} novos roteiros foram adicionados à pasta.")

elif menu == "🃏 Gerador de Flashcards":
    st.header("🃏 Gerador de Flashcards com IA")
    
    kb = load_knowledge_base()
    if not kb:
        st.warning("Vá na Base de Conhecimento e cadastre materiais primeiro!")
    else:
        materia_selecionada = st.selectbox("Qual Matéria?", list(kb.keys()))
        topicos = list(kb[materia_selecionada].keys())
        
        if not topicos:
            st.warning("Essa matéria não tem tópicos cadastrados.")
        else:
            topico_selecionado = st.selectbox("Qual Assunto?", topicos)
            
            estilos_flashcard = {
                "Direto ao Ponto (Conceito e Definição)": "Foco em perguntas diretas perguntando 'O que é...', 'Quais os requisitos...', etc. Respostas curtas e objetivas.",
                "Verdadeiro ou Falso (Estilo Cebraspe)": "Crie afirmações que podem ser verdadeiras ou falsas. A resposta deve dizer se é Verdadeiro ou Falso e explicar brevemente o porquê.",
                "Situação Prática (Estudo de Caso)": "Crie um cenário hipotético muito curto (1 ou 2 frases) e pergunte como a regra se aplica. A resposta deve resolver o caso de forma objetiva.",
                "Preencha as Lacunas": "Forneça uma frase com uma ou mais palavras-chave fundamentais ocultadas (usando '___'). A resposta deve conter as palavras que faltam para completar o sentido."
            }
            
            estilo_selecionado = st.selectbox("Qual o estilo dos Flashcards?", list(estilos_flashcard.keys()))
            qtd_flashcards = st.slider("Quantos flashcards gerar?", 3, 20, 5)
            
            texto_base = kb[materia_selecionada][topico_selecionado]
            
            if st.button("🧠 Gerar Flashcards"):
                with st.spinner(f"Gerando {qtd_flashcards} flashcards no estilo '{estilo_selecionado}'..."):
                    prompt_flashcards = f'''
                    Você é um professor especialista em criar material de revisão ativa (flashcards) para concursos públicos de alto nível.
                    Baseado no texto fornecido abaixo, crie {qtd_flashcards} flashcards focados no tópico '{topico_selecionado}'.
                    
                    O estilo dos flashcards deve ser OBRIGATORIAMENTE este:
                    {estilos_flashcard[estilo_selecionado]}
                    
                    A saída DEVE ser estritamente em um array JSON, seguindo exatamente esta estrutura:
                    [
                      {{"frente": "Texto da frente do card (Pergunta/Situação)", "verso": "Texto do verso do card (Resposta/Explicação)"}},
                      {{"frente": "...", "verso": "..."}}
                    ]
                    
                    Texto base extraído do material:
                    {texto_base}
                    '''
                    
                    try:
                        client = genai.Client(api_key=API_GEMINI)
                        response = client.models.generate_content(
                            model=MODELO_GEMINI,
                            contents=prompt_flashcards,
                            config=types.GenerateContentConfig(response_mime_type="application/json"),
                        )
                        flashcards_json = json.loads(response.text)
                        
                        st.success("Flashcards gerados com sucesso! Passe o mouse sobre os cartões para virar.")
                        
                        st.markdown("""
                        <style>
                        .flashcard-grid {
                            display: grid;
                            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                            gap: 20px;
                            margin-top: 20px;
                            margin-bottom: 30px;
                        }
                        .flip-card {
                            background-color: transparent;
                            width: 100%;
                            height: 280px;
                            perspective: 1000px;
                        }
                        .flip-card-inner {
                            position: relative;
                            width: 100%;
                            height: 100%;
                            text-align: center;
                            transition: transform 0.7s cubic-bezier(0.4, 0.2, 0.2, 1);
                            transform-style: preserve-3d;
                            cursor: pointer;
                        }
                        .flip-card:hover .flip-card-inner {
                            transform: rotateY(180deg);
                        }
                        .flip-card-front, .flip-card-back {
                            position: absolute;
                            width: 100%;
                            height: 100%;
                            backface-visibility: hidden;
                            display: flex;
                            flex-direction: column;
                            align-items: center;
                            justify-content: center;
                            border-radius: 15px;
                            box-shadow: 0 8px 16px rgba(0,0,0,0.3);
                            padding: 25px;
                            font-size: 16px;
                            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                            border: 2px solid #D4AF37;
                            overflow-y: auto;
                        }
                        .flip-card-front {
                            background-color: #0A192F;
                            color: #F3F4F6;
                        }
                        .flip-card-back {
                            background-color: #D4AF37;
                            color: #0A192F;
                            transform: rotateY(180deg);
                        }
                        .card-title {
                            position: absolute;
                            top: 15px;
                            left: 15px;
                            font-size: 12px;
                            text-transform: uppercase;
                            letter-spacing: 1px;
                            opacity: 0.8;
                            font-weight: bold;
                        }
                        .card-content {
                            margin-top: 15px;
                            line-height: 1.5;
                        }
                        </style>
                        """, unsafe_allow_html=True)
                        
                        st.markdown('<div class="flashcard-grid">', unsafe_allow_html=True)
                        
                        for i, card in enumerate(flashcards_json, 1):
                            frente = card.get('frente', '').replace('\n', '<br>')
                            verso = card.get('verso', '').replace('\n', '<br>')
                            
                            html_card = f"""
                            <div class="flip-card">
                              <div class="flip-card-inner">
                                <div class="flip-card-front">
                                  <div class="card-title">Frente | Cartão {i}</div>
                                  <div class="card-content"><b>{frente}</b></div>
                                </div>
                                <div class="flip-card-back">
                                  <div class="card-title">Verso | Resposta</div>
                                  <div class="card-content">{verso}</div>
                                </div>
                              </div>
                            </div>
                            """
                            st.markdown(html_card, unsafe_allow_html=True)
                            
                        st.markdown('</div>', unsafe_allow_html=True)
                                
                        json_string = json.dumps(flashcards_json, ensure_ascii=False, indent=2)
                        st.download_button(
                            label="📥 Baixar Flashcards (JSON) para Anki",
                            file_name=f"flashcards_{topico_selecionado.replace(' ', '_')}.json",
                            mime="application/json",
                            data=json_string
                        )
                            
                    except Exception as e:
                        st.error(f"Erro no Gemini: {e}")

elif menu in ["🎬 Renderizador Individual", "🏭 Renderização em Massa"]:
    st.header(menu)
    
    st.subheader("🎙️ Configuração de Áudio")
    vozes_disponiveis = {
        "Voz da Erica": "7f63edf2ac5f4e538992b065f5a20ce6",
        "Voz do Jean": "8d8c7204f55f440abf975500590c3c12",
        "Voz do Matheus": "8a7a95ba239d4475afcad5dbebb24a48"
    }
    
    voz_selecionada = st.selectbox("Selecione a Voz para a Narração", list(vozes_disponiveis.keys()))
    voice_id_selecionado = vozes_disponiveis[voz_selecionada]
    
    st.markdown("---")
    
    roteiros = [f for f in os.listdir("roteiros") if f.endswith('.json')]
    if not roteiros:
        st.warning("Nenhum roteiro gerado ainda.")
    else:
        for r in roteiros:
            st.write(f"- {r}")
        if st.button("🚀 Renderizar Vídeos"):
            for roteiro_file in roteiros:
                with st.spinner(f"Renderizando {roteiro_file}..."):
                    caminho_roteiro = os.path.join("roteiros", roteiro_file)
                    with open(caminho_roteiro, 'r', encoding='utf-8') as f:
                        dados = json.loads(f.read())
                    dicionario_global = {}
                    try:
                        with open("dicionario_fonetico.json", 'r', encoding='utf-8') as f:
                            dicionario_global = json.load(f)
                    except: pass
                        
                    st.info(f"Gerando áudio via Fish Audio TTS ({voz_selecionada})...")
                    audio_path = os.path.join("output", roteiro_file.replace('.json', '.mp3'))
                    
                    caminho_gerado, erro = gerar_audio_fishaudio(
                        texto=dados['roteiro_falado'],
                        dicionario_global=dicionario_global,
                        output_path=audio_path,
                        api_key=API_FISH,
                        voice_id=voice_id_selecionado 
                    )
                    
                    if caminho_gerado and os.path.exists(caminho_gerado):
                        st.info("Sincronizando tempos com Deepgram ASR...")
                        
                        deepgram_words, erro_asr = obter_timestamps_deepgram(caminho_gerado, API_DEEPGRAM)
                        
                        if erro_asr:
                            st.warning(f"Falha na API Deepgram. Usando tempos estimados. Motivo: {erro_asr}")
                        else:
                            st.success("Palavras mapeadas com sucesso! Aplicando timestamps precisos.")

                        st.info("Aplicando motor visual customizado...")
                        video_path = os.path.join("output", roteiro_file.replace('.json', '.mp4'))
                        
                        try:
                            render_keyword_video(dados, caminho_gerado, video_path, configuracoes_visuais, deepgram_words)
                            os.rename(caminho_roteiro, os.path.join("roteiros/feitos", roteiro_file))
                            st.success(f"🎉 Vídeo gerado com sucesso: {video_path}")
                        except Exception as e:
                            st.error(f"Erro durante a renderização: {e}")
                            
                        # Limpeza de memória
                        gc.collect()
                    else:
                        st.error(f"❌ Falha no Fish Audio: {erro}")

elif menu == "📥 Meus Vídeos (Output)":
    st.header("📥 Galeria de Vídeos Gerados")
    st.markdown("Aqui você pode visualizar e baixar os vídeos que já foram renderizados com sucesso.")
    
    videos = [f for f in os.listdir("output") if f.endswith(".mp4")]
    
    if not videos:
        st.info("Nenhum vídeo foi gerado ainda. Vá na aba de Renderização para criar seu primeiro vídeo!")
    else:
        for vid in videos:
            vid_path = os.path.join("output", vid)
            with st.expander(f"🎬 {vid}"):
                st.video(vid_path)
                with open(vid_path, "rb") as file:
                    st.download_button(
                        label="⬇️ Baixar Vídeo",
                        data=file,
                        file_name=vid,
                        mime="video/mp4",
                        key=f"dl_{vid}"
                    )

elif menu == "💾 Backup e Restauração":
    st.header("💾 Gerenciamento de Dados (Backup)")
    st.markdown("""
    O servidor na nuvem reinicia de tempos em tempos para liberar memória, o que pode apagar seus dados temporários. 
    **Faça o download da sua base de dados regularmente.** Caso o app reinicie, basta fazer o upload do arquivo salvo aqui para restaurar tudo!
    """)
    
    st.markdown("---")
    st.subheader("1. Fazer Backup (Baixar Base Atual)")
    
    if os.path.exists("base_conhecimento.json"):
        with open("base_conhecimento.json", "r", encoding="utf-8") as f:
            st.download_button(
                label="⬇️ Baixar base_conhecimento.json",
                data=f.read(),
                file_name="base_conhecimento.json",
                mime="application/json"
            )
    else:
        st.warning("O arquivo base_conhecimento.json ainda não existe ou está vazio.")
        
    st.markdown("---")
    st.subheader("2. Restaurar Backup (Subir Base Antiga)")
    uploaded_json = st.file_uploader("Faça o upload do seu arquivo base_conhecimento.json salvo", type="json")
    
    if uploaded_json is not None:
        if st.button("Restaurar Base de Conhecimento"):
            with open("base_conhecimento.json", "wb") as f:
                f.write(uploaded_json.getbuffer())
            st.success("✅ Base de conhecimento restaurada com sucesso! Você já pode voltar a gerar roteiros.")